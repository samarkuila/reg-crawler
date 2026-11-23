"""Behaviour only the SQL repository has: persistence, stored files, reference numbers."""

import hashlib
from datetime import date

import pytest

from app.db.refs import next_internal_ref
from app.db.session import make_engine, session_scope
from app.repositories import seed
from app.repositories.sql import SqlRepository
from app.schemas.entities import Document, SourceVersion
from app.schemas.enums import Cycle, FetchStatus, ItemState
from tests.conftest import make_backend


@pytest.fixture
def parts(tmp_path, sql_template):
    repo, url, files = make_backend("sql", tmp_path, sql_template)
    return repo, url, files


def test_changes_survive_a_new_repository_on_the_same_database(parts) -> None:
    repo, url, files = parts
    repo.add_manual_check("chk-2026-11-20-eod-egazette-html", "Divya G.", "ok", "Divya G.")
    repo.propose_setting("lookback-days", 4, "Samarendra K.", "Samarendra K.")
    run = repo.create_run(Cycle.ADHOC, date(2026, 11, 21), "Divya G.", "Divya G.")
    other = SqlRepository(make_engine(url), files_root=files)  # e.g. the app after a restart
    assert other.get_manual_check("chk-2026-11-20-eod-egazette-html").note == "ok"
    assert other.get_setting("lookback-days").pending.value == 4
    assert other.get_run(run.id).triggered_by == "Divya G."


def test_new_internal_refs_continue_after_the_seeded_ones(parts) -> None:
    repo, *_ = parts
    highest = max(
        i.internal_ref
        for i in repo.list_items(
            __import__("app.schemas.api", fromlist=["ItemFilter"]).ItemFilter()
        )
    )
    with session_scope(repo._factory) as session:  # noqa: SLF001
        ref = next_internal_ref(session, 2026)
    assert ref > highest and ref.startswith("TVSC-REG-2026-")
    assert int(ref[-6:]) == int(highest[-6:]) + 1  # the very next number, no gap


def test_stored_files_are_written_once_under_the_data_folder(parts) -> None:
    repo, _url, files = parts
    data = b"%PDF-1.4 first"
    digest = hashlib.sha256(data).hexdigest()
    path = f"data/documents/TVSC-REG-2026-000001/{digest}.pdf"
    doc = Document(
        id="doc-w",
        item_id="item-rbi-pdf-blocked",
        sha256=digest,
        path=path,
        fetch_status=FetchStatus.MANUAL,
        uploaded_by="Divya G.",
        fetched_at=seed.ist(21, 11, 9, 0),
    )
    repo.add_document(doc, data, ItemState.IN_REVIEW, "Divya G.")
    assert (files / path).read_bytes() == data
    # writing the same path again never replaces what is stored
    repo._write_once(path, b"something else")  # noqa: SLF001
    assert (files / path).read_bytes() == data


def test_a_stored_path_cannot_leave_the_data_folder(parts) -> None:
    repo, *_ = parts
    with pytest.raises(ValueError, match="escapes"):
        repo._file("../outside.txt")  # noqa: SLF001
    bad = Document(
        id="doc-bad",
        item_id="item-rbi-pdf-blocked",
        sha256="a" * 64,
        path="../../evil.txt",
        fetch_status=FetchStatus.MANUAL,
    )
    with pytest.raises(ValueError):
        repo.add_document(bad, b"x", None, "x")
    assert repo.get_document("doc-bad") is None  # the failed upload left no row behind


def test_a_failed_change_leaves_no_trace_in_the_database(parts) -> None:
    repo, *_ = parts
    before = len(repo.list_audit())
    ver = SourceVersion(
        id="srcv-rbi-1", source_id="rbi", version=1, requested_by="x", ts=seed.ist(21, 11, 9, 0)
    )
    with pytest.raises(ValueError):
        repo.create_source(repo.get_source("rbi"), ver, "x")
    with pytest.raises(Exception):  # noqa: B017 - the database refuses a missing parent
        repo.add_manual_check("nope", "x", "y", "x")
    assert len(repo.list_audit()) == before


def test_sources_keep_register_order_after_new_ones_are_added(parts) -> None:
    repo, *_ = parts
    first = [s.id for s in repo.list_sources()]
    new = repo.get_source("rbi").model_copy(update={"id": "zz-new", "current_version": 0})
    ver = SourceVersion(
        id="srcv-zz-new-1",
        source_id="zz-new",
        version=1,
        requested_by="x",
        ts=seed.ist(21, 11, 9, 0),
    )
    repo.create_source(new, ver, "x")
    assert [s.id for s in repo.list_sources()] == [*first, "zz-new"]


def test_load_is_for_empty_databases_only_by_convention(parts) -> None:
    repo, *_ = parts
    assert repo.is_empty() is False
