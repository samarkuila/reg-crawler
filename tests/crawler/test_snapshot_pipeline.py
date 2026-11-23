"""SnapshotPipeline: raw bytes stored write-once under data/snapshots/{source}/{date}/{sha256}."""

import hashlib
import sqlite3

import pytest

from app.schemas.enums import CheckChannel
from regcrawler.items import ListingItem, SnapshotItem
from regcrawler.pipelines import SnapshotPipeline
from tests.crawler.conftest import fixture_bytes

PAGE = fixture_bytes("html", "rbi_listing_sample.html")
SHA = hashlib.sha256(PAGE).hexdigest()


def snapshot_item(spider, body: bytes = PAGE) -> SnapshotItem:
    return SnapshotItem(
        source_id=spider.source.id,
        run_id=spider.run.id,
        channel="HTML",
        url="https://rbi.org.in/notifications",
        body=body,
        http_status=200,
    )


def test_bytes_are_stored_hashed_and_recorded(crawler, repo, spider) -> None:
    out = SnapshotPipeline(crawler, repo).process_item(snapshot_item(spider))
    snap = repo.get_snapshot(out["snapshot_id"])
    assert snap.sha256 == SHA
    assert snap.path.startswith(f"data/snapshots/{spider.source.id}/")
    assert snap.path.endswith(f"/{SHA}")
    assert repo.read_blob(SHA) == PAGE  # byte for byte
    assert "body" not in out  # the bytes do not travel on through the other pipelines
    assert spider.check.state(CheckChannel.HTML).snapshot_id == snap.id


def test_listing_rows_point_at_the_snapshot_of_their_channel(crawler, repo, spider) -> None:
    pipeline = SnapshotPipeline(crawler, repo)
    snap = pipeline.process_item(snapshot_item(spider))
    row = pipeline.process_item(ListingItem(channel="HTML", url="https://rbi.org.in/a", title="A"))
    assert row["snapshot_id"] == snap["snapshot_id"]


def test_same_bytes_give_one_file_and_two_rows(crawler, repo, spider) -> None:
    pipeline = SnapshotPipeline(crawler, repo)
    first = pipeline.process_item(snapshot_item(spider))
    second = pipeline.process_item(snapshot_item(spider))
    assert first["snapshot_id"] != second["snapshot_id"]
    paths = {repo.get_snapshot(s["snapshot_id"]).path for s in (first, second)}
    assert len(paths) == 1
    assert repo.read_blob(SHA) == PAGE


def test_changed_page_is_a_new_snapshot_and_the_old_one_stays(crawler, repo, spider) -> None:
    pipeline = SnapshotPipeline(crawler, repo)
    old = pipeline.process_item(snapshot_item(spider))
    new = pipeline.process_item(snapshot_item(spider, PAGE + b"<!-- changed -->"))
    assert repo.get_snapshot(old["snapshot_id"]).sha256 == SHA
    assert repo.get_snapshot(new["snapshot_id"]).sha256 != SHA
    assert repo.read_blob(SHA) == PAGE


def test_an_existing_stored_file_is_never_overwritten(sql_env) -> None:
    pipeline = SnapshotPipeline(sql_env.crawler, sql_env.repo)
    out = pipeline.process_item(snapshot_item(sql_env.spider))
    stored = sql_env.files / sql_env.repo.get_snapshot(out["snapshot_id"]).path
    stored.write_bytes(b"tampered")  # someone edits the stored file...
    pipeline.process_item(snapshot_item(sql_env.spider))  # ...a re-fetch must not fix it up
    assert stored.read_bytes() == b"tampered"  # write-once means never rewritten


def test_a_stored_snapshot_row_cannot_be_updated_or_deleted(sql_env) -> None:
    out = SnapshotPipeline(sql_env.crawler, sql_env.repo).process_item(
        snapshot_item(sql_env.spider)
    )
    con = sqlite3.connect(sql_env.url.removeprefix("sqlite:///"))
    try:
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("UPDATE snapshot SET sha256='0' WHERE id=?", (out["snapshot_id"],))
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("DELETE FROM snapshot WHERE id=?", (out["snapshot_id"],))
    finally:
        con.close()
