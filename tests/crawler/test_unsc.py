"""UNSC list: diff (added / removed / amended) and the spider that applies it to snapshots."""

import asyncio

import pytest

from app.schemas.entities import SourceVersion, now_ist
from app.schemas.enums import CheckChannel, CheckStatus, VersionStatus
from regcrawler import unsc
from regcrawler.spiders.unsc_xml import UnscXmlSpider
from tests.crawler.conftest import (
    FIXTURES,
    check_of,
    crawl,
    file_response,
    make_spider,
    stub_crawler,
)

URL = "https://scsanctions.un.org/resources/xml/en/consolidated.xml"
BASE, CHANGED = "xml/unsc_baseline.xml", "xml/unsc_changed.xml"


def listing(name: str) -> unsc.Listing:
    return unsc.parse_consolidated((FIXTURES / name).read_bytes())


# --- the diff ----------------------------------------------------------------------------------


def test_the_list_is_read_by_reference_number() -> None:
    entries = listing(BASE).entries
    assert set(entries) == {"QDi.901", "TAi.902", "QDi.903", "QDe.904"}
    assert entries["QDi.901"].name == "ALPHA TESTNAME"
    assert entries["QDi.901"].aliases == ("Alpha T.",)
    assert entries["QDe.904"].kind == "entity" and entries["QDe.904"].aliases == ("Delta Co",)


def test_identical_lists_have_no_differences() -> None:
    assert unsc.diff(listing(BASE), listing(BASE)) == []


def changes() -> dict[str, unsc.Difference]:
    return {d.ref: d for d in unsc.diff(listing(BASE), listing(CHANGED))}


def test_an_entry_only_in_the_new_list_is_added() -> None:
    added = changes()["TAi.905"]
    assert added.change == unsc.Change.ADDED
    assert added.name == "ECHO NEWLY" and added.listed_on == "2026-11-20"


def test_an_entry_only_in_the_old_list_is_removed() -> None:
    removed = changes()["TAi.902"]
    assert removed.change == unsc.Change.REMOVED and removed.name == "BRAVO SAMPLE"


def test_a_new_alias_is_an_amendment_that_names_the_alias() -> None:
    amended = changes()["QDi.901"]
    assert amended.change == unsc.Change.AMENDED
    assert amended.aliases_added == ("Al Fa",) and amended.aliases_removed == ()
    assert amended.summary == "alias added: Al Fa; details changed: versionnum"
    assert amended.aliases == ("Al Fa", "Alpha T.")


def test_a_changed_detail_is_an_amendment_that_names_the_field() -> None:
    amended = changes()["QDi.903"]
    assert amended.change == unsc.Change.AMENDED
    assert "individual date of birth" in amended.changed_details
    assert amended.aliases_added == ()


def test_an_untouched_entry_is_not_reported() -> None:
    assert "QDe.904" not in changes()
    assert set(changes()) == {"TAi.905", "TAi.902", "QDi.901", "QDi.903"}


def test_a_removed_alias_and_a_renamed_entry_are_reported() -> None:
    old, new = listing(BASE), listing(BASE)
    entry = new.entries["QDi.901"]
    new.entries["QDi.901"] = unsc.Entry(
        ref=entry.ref,
        kind=entry.kind,
        name="ALPHA RENAMED",
        aliases=(),
        listed_on=entry.listed_on,
        details=entry.details,
    )
    (diff,) = unsc.diff(old, new)
    assert diff.aliases_removed == ("Alpha T.",) and diff.name_before == "ALPHA TESTNAME"
    assert "name changed from ALPHA TESTNAME" in diff.summary


def test_alias_comparison_ignores_case_and_spacing() -> None:
    old = listing(BASE)
    new_xml = (FIXTURES / BASE).read_text(encoding="utf-8").replace("Alpha T.", "ALPHA   t.")
    assert unsc.diff(old, unsc.parse_consolidated(new_xml.encode())) == []


@pytest.mark.parametrize(
    "content",
    [b"", b"not xml at all", b"<html><body>Access denied</body></html>", b"<a><b></a>"],
)
def test_unreadable_files_raise(content) -> None:
    with pytest.raises(unsc.UnscError):
        unsc.parse_consolidated(content)


def test_xml_entity_tricks_are_refused() -> None:
    bomb = (
        b'<?xml version="1.0"?><!DOCTYPE x [<!ENTITY a "aaaa">]>'
        b"<CONSOLIDATED_LIST>&a;</CONSOLIDATED_LIST>"
    )
    with pytest.raises(unsc.UnscError):
        unsc.parse_consolidated(bomb)


# --- the spider --------------------------------------------------------------------------------


@pytest.fixture
def unsc_id(repo) -> str:
    """An active UNSC source with no history (the demo seed's own source has old snapshots)."""
    template = repo.get_source("unsc")
    source = template.model_copy(update={"id": "unsc-test", "current_version": 1})
    version = SourceVersion(
        id="sv-unsc-test",
        source_id="unsc-test",
        version=1,
        after=source.model_dump(mode="json"),
        status=VersionStatus.APPROVED,
        requested_by="tester",
        approved_by="approver",
        ts=now_ist(),
    )
    repo.create_source(source, version, "tester")
    return source.id


@pytest.fixture
def run(repo, unsc_id):
    """Crawl `name` as a fresh run of the unsc source; returns (items, spider, check)."""

    def go(name: str, **kwargs):
        spider = make_spider(UnscXmlSpider, repo, unsc_id, **kwargs)
        url = spider.fixture.as_uri() if spider.fixture else URL  # as the downloader would see it
        items = crawl(spider, file_response(name, url), stub_crawler(spider, repo))
        return items, spider, check_of(repo, spider, CheckChannel.XML)

    return go


def test_the_first_run_stores_a_baseline_and_emits_nothing(run) -> None:
    items, _spider, check = run(BASE)
    assert items == []
    assert check.status == CheckStatus.OK and check.items_found == 0
    assert "Baseline stored (4 entries)" in check.error_detail and check.snapshot_id


def test_an_unchanged_list_is_ok_not_zero_items(run) -> None:
    run(BASE)
    items, _spider, check = run(BASE)
    assert items == [] and check.status == CheckStatus.OK


def test_a_changed_list_emits_urgent_items_for_each_change(run) -> None:
    run(BASE)
    items, _spider, check = run(CHANGED)
    assert check.status == CheckStatus.OK and check.items_found == 4
    by_ref = {i["ref_no"]: i for i in items}
    assert {r: i["raw_extra"]["change"] for r, i in by_ref.items()} == {
        "TAi.905": "ADDED",
        "QDi.901": "AMENDED",
        "QDi.903": "AMENDED",
        "TAi.902": "REMOVED",
    }
    assert all(i["raw_extra"]["priority"] == "URGENT" and i["channel"] == "XML" for i in items)
    alias = by_ref["QDi.901"]
    assert alias["raw_extra"]["aliases_added"] == ["Al Fa"]
    assert alias["title"] == (
        "UNSC sanctions list amended: ALPHA TESTNAME (QDi.901) - "
        "alias added: Al Fa; details changed: versionnum"
    )
    assert alias["url"] == f"{URL}?reference=QDi.901"
    assert by_ref["TAi.905"]["displayed_date"].isoformat() == "2026-11-20"


def test_items_say_which_snapshot_they_were_compared_with(run, repo) -> None:
    _items, first_spider, first = run(BASE)
    items, _spider, _check = run(CHANGED)
    assert {i["raw_extra"]["compared_with_snapshot_id"] for i in items} == {first.snapshot_id}
    assert repo.get_snapshot(first.snapshot_id).url == URL


def test_both_files_are_stored_as_snapshots(run, repo) -> None:
    _i, _s, first = run(BASE)
    _i, _s, second = run(CHANGED)
    assert first.snapshot_id != second.snapshot_id
    old, new = (repo.get_snapshot(c.snapshot_id) for c in (first, second))
    assert repo.read_blob(old.sha256) == (FIXTURES / BASE).read_bytes()
    assert repo.read_blob(new.sha256) == (FIXTURES / CHANGED).read_bytes()


def test_a_list_that_is_empty_is_a_parse_error_not_a_mass_removal(run, repo) -> None:
    run(BASE)
    items, _spider, check = run("xml/unsc_empty.xml")
    assert items == []
    assert check.status == CheckStatus.PARSE_ERROR and "no entries" in check.error_detail
    assert check.snapshot_id  # kept as evidence


def test_a_malformed_download_is_a_parse_error_and_not_the_next_baseline(run) -> None:
    run(BASE)
    _i, _s, bad = run("xml/unsc_malformed.xml")
    assert bad.status == CheckStatus.PARSE_ERROR
    items, _spider, check = run(CHANGED)  # still compared with the last HEALTHY snapshot
    assert check.items_found == 4 and len(items) == 4


def test_a_blocked_run_is_not_a_baseline(repo, unsc_id) -> None:
    from regcrawler.pipelines import DbPipeline

    spider = make_spider(UnscXmlSpider, repo, unsc_id)
    spider.check.block(CheckChannel.XML, "HTTP 403", 403)
    DbPipeline(stub_crawler(spider, repo), repo).close_spider()
    assert spider.previous_snapshot(CheckChannel.XML) is None


def test_live_and_offline_snapshots_are_never_mixed(repo, run, unsc_id) -> None:
    run(BASE, fixture=str(FIXTURES / BASE))  # offline run: stored with a file:// address
    spider = make_spider(UnscXmlSpider, repo, unsc_id)  # a live run
    assert spider.previous_snapshot(CheckChannel.XML) is None


def test_the_spider_requests_the_source_url(repo) -> None:
    spider = make_spider(UnscXmlSpider, repo, "unsc")

    async def collect():
        return [r async for r in spider.start()]

    (request,) = asyncio.run(collect())
    assert request.url == URL and request.meta["channel"] == "XML"
