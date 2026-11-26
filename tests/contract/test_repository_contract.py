"""One contract, two backends: every test here runs on the in-memory AND the SQL repository.

If a test passes on one backend and fails on the other, the backends disagree and the UI would
behave differently. `test_both_backends_return_identical_results` compares them side by side.
"""

import hashlib
import inspect
import re
import sqlite3
from datetime import date

import pytest

from app.repositories import seed
from app.repositories.base import Repository
from app.schemas.api import CycleRef, ItemFilter
from app.schemas.entities import (
    DailyUpdate,
    DailyUpdateItem,
    Document,
    RawListingItem,
    ReviewDecision,
    Snapshot,
    SourceVersion,
    now_ist,
)
from app.schemas.enums import CheckChannel, Cycle, FetchStatus, ItemState, Treatment, UpdateStatus
from tests.conftest import BACKENDS, make_backend

MUTATING = {
    "create_source",
    "create_sources_batch",
    "set_source_baseline",
    "add_seen_fingerprints",
    "add_source_version",
    "approve_source_version",
    "reject_source_version",
    "create_run",
    "record_source_check",
    "record_snapshot",
    "add_raw_items",
    "save_detection",
    "close_run",
    "save_extraction",
    "add_agent_run",
    "add_agent_flags",
    "resolve_agent_flag",
    "add_alerts",
    "mark_items_seen",
    "add_manual_check",
    "add_document",
    "save_decisions",
    "save_daily_update",
    "approve_daily_update",
    "mark_daily_update_exported",
    "propose_setting",
    "approve_setting",
    "reject_setting",
}
EGAZETTE_CHECK = "chk-2026-11-20-eod-egazette-html"
EOD = CycleRef(cycle_date=date(2026, 11, 20), cycle=Cycle.EOD)


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path, sql_template):
    repository, _url, _files = make_backend(request.param, tmp_path, sql_template)
    return repository


@pytest.fixture
def both(tmp_path, sql_template):
    memory, *_ = make_backend("memory", tmp_path, sql_template)
    sql, *_ = make_backend("sql", tmp_path, sql_template)
    return memory, sql


def protocol_methods() -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(fn)
        for name, fn in inspect.getmembers(Repository, inspect.isfunction)
        if not name.startswith("_")
    }


def decision(item_id: str, **extra) -> ReviewDecision:
    base = {
        "id": f"dec-{item_id}",
        "item_id": item_id,
        "reviewer": "Divya G.",
        "decided_at": seed.ist(21, 11, 9, 0),
        "treatment": Treatment.INFO_ONLY,
        "include_in_update": True,
    }
    return ReviewDecision(**{**base, **extra})


# ---------- the protocol ----------


def test_every_mutating_method_takes_an_actor() -> None:
    with_actor = {n for n, sig in protocol_methods().items() if "actor" in sig.parameters}
    assert with_actor - {"list_audit"} == MUTATING  # list_audit filters by actor; it only reads


def test_repository_implements_the_whole_protocol(repo) -> None:
    for name, sig in protocol_methods().items():
        impl = getattr(repo, name, None)
        assert impl is not None, f"{type(repo).__name__}.{name} missing"
        assert list(inspect.signature(impl).parameters) == [
            p for p in sig.parameters if p != "self"
        ], name


def test_evidence_tables_are_append_only() -> None:
    names = set(protocol_methods())
    assert not [n for n in names if re.match(r"(delete|remove|update|edit)_", n)]
    assert "list_audit" in names and not [n for n in names if "audit" in n and n != "list_audit"]


# ---------- the seed data (identical on both) ----------


def test_seed_sources_keep_register_order(repo) -> None:
    sources = repo.list_sources()
    assert [s.id for s in sources][:5] == ["rbi", "unsc", "trai", "irdai", "egazette"]
    assert len([s for s in sources if s.active]) == 5
    planned = [s for s in sources if not s.active]
    assert len(planned) == 8 and all(s.planned and s.current_version == 0 for s in planned)


def test_seed_run_and_blocked_check(repo) -> None:
    (run,) = repo.find_runs(EOD)
    checks = repo.list_source_checks(run.id)
    assert [c.source_id for c in checks if c.status.value == "BLOCKED"] == ["egazette"]
    assert sum(1 for c in checks if c.status.value == "OK") == 5


def test_seed_users(repo) -> None:
    users = repo.list_users()
    assert [u.name for u in users] == ["Divya G.", "George K.", "Samarendra K.", "Audit"]
    assert {u.name: u.role.value for u in users} == {
        "Divya G.": "reviewer",
        "George K.": "approver",
        "Samarendra K.": "admin",
        "Audit": "viewer",
    }


def test_seed_items_are_consistent(repo) -> None:
    items = repo.list_items(ItemFilter())
    assert len(items) == 10
    assert len({i.fingerprint for i in items}) == len(items)
    assert len({i.internal_ref for i in items}) == len(items)
    sources = {s.id for s in repo.list_sources()}
    for i in items:
        assert re.fullmatch(r"TVSC-REG-\d{4}-\d{6}", i.internal_ref)
        assert i.source_id in sources and re.fullmatch(r"[0-9a-f]{64}", i.fingerprint)
        assert repo.list_observations(i.id), i.id
    states = {i.id: i.state for i in items}
    assert states["item-pib-2336151"] == ItemState.DECIDED
    assert states["item-rbi-pdf-blocked"] == ItemState.NEEDS_MANUAL_FETCH


def test_snapshots_have_sha256_and_documents_are_hashed(repo) -> None:
    for obs in repo.list_observations("item-iors-faqs"):
        if obs.snapshot_id:
            assert re.fullmatch(r"[0-9a-f]{64}", repo.get_snapshot(obs.snapshot_id).sha256)
    doc = repo.list_documents("item-iors-faqs")[0]
    assert re.fullmatch(r"[0-9a-f]{64}", doc.sha256)
    assert repo.read_blob(doc.sha256) is not None


def test_default_settings_from_the_brief(repo) -> None:
    keys = {s.key for s in repo.list_settings()}
    assert {"entity-profile", "screening-guidance", "relevance-keywords"} <= keys
    guidance = repo.get_setting("screening-guidance").value["text"]
    assert guidance.startswith("A regulatory update is anything where a regulator requires")
    assert "vacancies, tenders, events and maintenance notices" in guidance
    assert "KYC" in repo.get_setting("relevance-keywords").value
    assert "gross direct premium" in repo.get_setting("exclusion-keywords").value
    profile = repo.get_setting("entity-profile").value
    assert "PLACEHOLDER" in profile["status"] and all(i["held"] is None for i in profile["items"])


# ---------- behaviour ----------


def test_returned_objects_are_copies(repo) -> None:
    item = repo.get_item("item-iors-faqs")
    item.title = "changed"
    item.state = ItemState.PUBLISHED
    assert repo.get_item("item-iors-faqs").title != "changed"
    events = repo.list_audit()
    events[0].actor = "tamper"
    assert repo.list_audit()[0].actor != "tamper"
    doc = repo.list_documents("item-iors-faqs")[0]
    doc.sha256 = "0" * 64
    assert repo.list_documents("item-iors-faqs")[0].sha256 != "0" * 64


def test_failed_mutation_rolls_back_everything(repo, monkeypatch) -> None:
    n_audit = len(repo.list_audit())
    calls = {"n": 0}
    real = repo._audit

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # fail on the state-change event, after the decision was stored
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "_audit", flaky)
    dismissed = decision("item-trai-subs", treatment=Treatment.NOT_RELEVANT, dismiss_reason="x")
    with pytest.raises(RuntimeError):
        repo.save_decisions([dismissed], ItemState.DECIDED, "Divya G.")
    assert repo.list_decisions("item-trai-subs") == []
    assert repo.get_item("item-trai-subs").state == ItemState.DETECTED
    assert len(repo.list_audit()) == n_audit


def test_mutation_audit_event_is_written_with_the_actor(repo) -> None:
    check = repo.add_manual_check(EGAZETTE_CHECK, "Divya G.", "ok", "Divya G.")
    event = repo.list_audit()[0]
    assert (event.actor, event.action, event.entity) == (
        "Divya G.",
        "manual_check.added",
        "manual_check",
    )
    assert event.entity_id == check.id and event.after["note"] == "ok" and event.before is None
    assert repo.get_manual_check(EGAZETTE_CHECK).note == "ok"


def test_each_mutation_and_its_audit_event_land_together(repo) -> None:
    n = len(repo.list_audit())
    repo.save_decisions([decision("item-trai-subs")], ItemState.DECIDED, "Divya G.")
    new = repo.list_audit()[: len(repo.list_audit()) - n]
    assert {e.action for e in new} == {
        "review_decision.accepted",
        "regulatory_item.state_changed",
    }
    assert repo.get_item("item-trai-subs").state == ItemState.DECIDED


def test_unknown_or_invalid_requests_raise_the_same_errors(repo) -> None:
    with pytest.raises(KeyError):
        repo.add_manual_check("nope", "x", "y", "x")
    with pytest.raises(KeyError):
        repo.save_decisions([decision("nope")], ItemState.DECIDED, "x")
    with pytest.raises(KeyError):
        repo.propose_setting("nope", 1, "x", "x")
    with pytest.raises(KeyError):
        repo.add_source_version("nope", {"priority": "URGENT"}, "x")
    with pytest.raises(ValueError):
        repo.approve_setting("lookback-days", "George K.", "George K.")  # nothing pending
    with pytest.raises(ValueError):
        repo.approve_source_version("egazette", 2, "George K.", "George K.")  # already approved
    with pytest.raises(ValueError):
        repo.create_source(repo.get_source("rbi"), _version("rbi", 9), "x")  # duplicate
    assert repo.get_item("nope") is None and repo.get_source("nope") is None
    assert repo.get_setting("nope") is None and repo.get_document("nope") is None


def _version(source_id: str, n: int) -> SourceVersion:
    return SourceVersion(
        id=f"srcv-{source_id}-{n}",
        source_id=source_id,
        version=n,
        requested_by="x",
        ts=seed.ist(21, 11, 9, 0),
    )


def test_source_version_flow(repo) -> None:
    v = repo.add_source_version("trai", {"priority": "URGENT"}, "Samarendra K.")
    assert (v.version, v.status.value, v.before) == (2, "PENDING", {"priority": "NORMAL"})
    assert repo.get_source("trai").priority.value == "NORMAL"  # not live until approved
    approved = repo.approve_source_version("trai", 2, "George K.", "George K.")
    assert approved.approved_by == "George K." and approved.decided_at is not None
    trai = repo.get_source("trai")
    assert trai.priority.value == "URGENT" and trai.current_version == 2
    w = repo.add_source_version("trai", {"fetch_engine": "browser"}, "Samarendra K.")
    rejected = repo.reject_source_version("trai", w.version, "George K.", "Too slow", "George K.")
    assert rejected.status.value == "REJECTED" and rejected.reject_reason == "Too slow"
    assert [x.version for x in repo.list_source_versions("trai")] == [1, 2, 3] or [
        x.version for x in repo.list_source_versions("trai")
    ] == [2, 3]
    assert repo.get_source("trai").fetch_engine.value == "http"  # the rejected change is not live


def test_approving_a_new_sources_first_version_activates_it(repo) -> None:
    source = repo.get_source("rbi").model_copy(update={"id": "sebi", "active": False})
    repo.create_source(source.model_copy(update={"current_version": 0}), _version("sebi", 1), "x")
    assert repo.list_sources()[-1].id == "sebi"  # new sources go to the end of the register
    pending = repo.get_source_version("sebi", 1)
    assert pending.status.value == "PENDING"
    repo.approve_source_version("sebi", 1, "George K.", "George K.")
    assert repo.get_source("sebi").current_version == 1


def test_setting_flow_and_the_three_config_views(repo) -> None:
    s = repo.propose_setting("lookback-days", 3, "Samarendra K.", "Samarendra K.")
    assert s.value == 2 and s.pending.value == 3 and s.pending.version == 2
    s = repo.approve_setting("lookback-days", "George K.", "George K.")
    assert (s.value, s.version, s.pending) == (3, 2, None)
    repo.propose_setting("holidays", ["26.01.2027"], "Samarendra K.", "Samarendra K.")
    s = repo.reject_setting("holidays", "Not yet", "George K.")
    assert s.value == [] and s.pending is None
    assert [f.id for f in repo.list_sme_functions()][:2] == ["treasury", "finance"]
    assert [r.kind.value for r in repo.list_recipients()] == ["TO", "TO"]
    assert repo.get_email_template().greeting == "Dear All,"


def test_daily_update_lifecycle(repo) -> None:
    update = DailyUpdate(
        id="upd-x",
        cycle_date=date(2026, 11, 21),
        cycle=Cycle.MORNING,
        subject="S",
        html="<p>",
        prepared_by=["Divya G."],
    )
    rows = [
        DailyUpdateItem(
            update_id="upd-x",
            item_id="item-pib-2336151",
            decision_id="dec-pib-2336151",
            serial_no=1,
        )
    ]
    repo.save_daily_update(update, rows, "Divya G.")
    assert (
        repo.find_daily_update(CycleRef(cycle_date=date(2026, 11, 21), cycle=Cycle.MORNING)).id
        == "upd-x"
    )
    assert [r.serial_no for r in repo.list_daily_update_items("upd-x")] == [1]
    repo.save_daily_update(
        update, [rows[0].model_copy(update={"serial_no": 2})], "Divya G."
    )  # renumber
    assert [r.serial_no for r in repo.list_daily_update_items("upd-x")] == [2]
    assert {u.id for u in repo.list_daily_updates_for_item("item-pib-2336151")} == {
        "upd-2026-11-20-eod",
        "upd-x",
    }
    approved = repo.approve_daily_update("upd-x", "George K.", "George K.")
    assert approved.status == UpdateStatus.APPROVED and approved.approved_at is not None
    assert repo.get_item("item-pib-2336151").state == ItemState.PUBLISHED
    with pytest.raises(ValueError):
        repo.save_daily_update(update, rows, "Divya G.")  # a draft only
    exported = repo.mark_daily_update_exported("upd-x", "Divya G.")
    assert exported.status == UpdateStatus.EXPORTED and exported.exported_at is not None
    assert repo.mark_daily_update_exported("upd-x", "Divya G.").exported_at == exported.exported_at


def test_add_document_stores_the_file_and_moves_the_item(repo) -> None:
    data = b"%PDF-1.4 manual upload"
    import hashlib

    digest = hashlib.sha256(data).hexdigest()
    doc = Document(
        id="doc-up",
        item_id="item-rbi-pdf-blocked",
        sha256=digest,
        path=f"data/documents/TVSC-REG/{digest}.pdf",
        mime="application/pdf",
        fetch_status=FetchStatus.MANUAL,
        uploaded_by="Divya G.",
        fetched_at=seed.ist(21, 11, 9, 5),
        filename="circular.pdf",
    )
    repo.add_document(doc, data, ItemState.IN_REVIEW, "Divya G.")
    assert repo.get_document("doc-up").filename == "circular.pdf"
    assert repo.read_blob(digest) == data
    assert repo.get_item("item-rbi-pdf-blocked").state == ItemState.IN_REVIEW
    assert [d.id for d in repo.list_documents("item-rbi-pdf-blocked")][-1] == "doc-up"


# ---------- reads ----------


def test_list_items_filters(repo) -> None:
    all_ids = {i.id for i in repo.list_items(ItemFilter())}
    in_cycle = {i.id for i in repo.list_items(ItemFilter(cycle=EOD))}
    assert in_cycle and in_cycle <= all_ids
    assert {i.id for i in repo.list_items(ItemFilter(q="iors"))} == {"item-iors-faqs"}
    assert {i.id for i in repo.list_items(ItemFilter(q="  IORS "))} == {"item-iors-faqs"}
    assert {i.id for i in repo.list_items(ItemFilter(q="tvsc-reg-2026-000214"))} == {
        "item-iors-faqs"
    }
    assert repo.list_items(ItemFilter(q="%")) == []  # a wildcard is text, not a pattern
    assert {i.state for i in repo.list_items(ItemFilter(state=ItemState.DECIDED))} == {
        ItemState.DECIDED
    }
    assert all(i.relevance_score >= 50 for i in repo.list_items(ItemFilter(min_relevance=50)))
    items = repo.list_items(ItemFilter())
    assert [(i.first_seen_at, i.id) for i in items] == sorted(
        (i.first_seen_at, i.id) for i in items
    )


def test_runs_and_cycles_newest_first(repo) -> None:
    assert [c.id for c in repo.list_cycles()] == [
        "2026-11-20-EOD",
        "2026-11-20-MORNING",
        "2026-11-19-EOD",
        "2026-11-19-MORNING",
        "2026-11-03-EOD",
    ]
    assert [r.id for r in repo.list_runs(date(2026, 11, 20))] == [
        "run-2026-11-20-eod",
        "run-2026-11-20-am",
    ]
    run = repo.create_run(Cycle.ADHOC, date(2026, 11, 21), "Divya G.", "Divya G.")
    assert repo.get_run(run.id).status.value == "RUNNING" and repo.list_cycles()[0].id.endswith(
        "ADHOC"
    )


def test_audit_filters_use_ist_days(repo) -> None:
    all_events = repo.list_audit()
    assert [(e.ts, e.id) for e in all_events] == sorted(
        ((e.ts, e.id) for e in all_events), reverse=True
    )
    day = repo.list_audit(date_from=date(2026, 11, 20), date_to=date(2026, 11, 20))
    assert day and all(e.ts.date() == date(2026, 11, 20) for e in day)
    assert {e.actor for e in repo.list_audit(actor="divya g.")} == {"Divya G."}
    assert {e.entity for e in repo.list_audit(entity="crawl_run")} == {"crawl_run"}
    assert [e.entity_id for e in repo.list_audit(entity="srcv-egazette-3")] == ["srcv-egazette-3"]
    assert repo.list_audit(date_from=date(2030, 1, 1)) == []


def test_datetimes_come_back_in_ist(repo) -> None:
    run = repo.get_run("run-2026-11-20-eod")
    assert run.started_at.utcoffset().total_seconds() == 5.5 * 3600
    assert run.started_at == seed.ist(20, 11, 19, 30)


# ---------- identical results ----------

READS = {
    "users": lambda r: r.list_users(),
    "sources": lambda r: r.list_sources(),
    "versions": lambda r: [v for s in r.list_sources() for v in r.list_source_versions(s.id)],
    "runs": lambda r: r.list_runs(),
    "cycles": lambda r: r.list_cycles(),
    "checks": lambda r: r.list_source_checks(),
    "manual": lambda r: [r.get_manual_check(c.id) for c in r.list_source_checks()],
    "items": lambda r: r.list_items(ItemFilter()),
    "items_in_cycle": lambda r: r.list_items(ItemFilter(cycle=EOD)),
    "observations": lambda r: [
        o for i in r.list_items(ItemFilter()) for o in r.list_observations(i.id)
    ],
    "snapshots": lambda r: [
        r.get_snapshot(o.snapshot_id)
        for i in r.list_items(ItemFilter())
        for o in r.list_observations(i.id)
        if o.snapshot_id
    ],
    "documents": lambda r: [d for i in r.list_items(ItemFilter()) for d in r.list_documents(i.id)],
    "decisions": lambda r: [d for i in r.list_items(ItemFilter()) for d in r.list_decisions(i.id)],
    "update": lambda r: r.find_daily_update(EOD),
    "update_rows": lambda r: r.list_daily_update_items("upd-2026-11-20-eod"),
    "updates_for_item": lambda r: r.list_daily_updates_for_item("item-pib-2336151"),
    "settings": lambda r: r.list_settings(),
    "sme": lambda r: r.list_sme_functions(),
    "recipients": lambda r: r.list_recipients(),
    "template": lambda r: r.get_email_template(),
    "audit": lambda r: r.list_audit(),
}


def dump(value):
    if isinstance(value, list):
        return [dump(v) for v in value]
    return None if value is None else value.model_dump(mode="json")


@pytest.mark.parametrize("name", sorted(READS))
def test_both_backends_return_identical_results(both, name) -> None:
    memory, sql = both
    assert dump(READS[name](memory)) == dump(READS[name](sql))


def test_stored_copies_are_identical(both) -> None:
    memory, sql = both
    for snap in READS["snapshots"](sql) + READS["documents"](sql):
        if snap.sha256:
            assert memory.read_blob(snap.sha256) == sql.read_blob(snap.sha256)


def run_script(repo) -> list:
    """The same sequence of changes; returns only values that must match across backends."""
    trace: list = []
    trace.append(
        repo.save_decisions([decision("item-trai-subs")], ItemState.DECIDED, "Divya G.")[0].is_draft
    )
    repo.save_decisions(
        [decision("item-irdai-agents", id="dec-2", decided_at=seed.ist(21, 11, 9, 1))],
        ItemState.IN_REVIEW,
        "Divya G.",
    )
    repo.add_manual_check(EGAZETTE_CHECK, "Divya G.", "checked", "Divya G.")
    trace.append(repo.get_manual_check(EGAZETTE_CHECK).note)
    repo.add_source_version(
        "trai", {"priority": "URGENT", "include_keywords": ["x"]}, "Samarendra K."
    )
    trace.append(repo.approve_source_version("trai", 2, "George K.", "George K.").status.value)
    trace.append(repo.get_source("trai").model_dump(mode="json"))
    repo.propose_setting("lookback-days", 5, "Samarendra K.", "Samarendra K.")
    trace.append(
        repo.approve_setting("lookback-days", "George K.", "George K.").model_dump(
            mode="json", exclude={"updated_at"}
        )
    )
    update = DailyUpdate(id="upd-s", cycle_date=date(2026, 11, 21), cycle=Cycle.EOD, subject="S")
    rows = [
        DailyUpdateItem(
            update_id="upd-s",
            item_id="item-trai-subs",
            decision_id="dec-item-trai-subs",
            serial_no=1,
        )
    ]
    repo.save_daily_update(update, rows, "Divya G.")
    repo.approve_daily_update("upd-s", "George K.", "George K.")
    trace.append(repo.get_item("item-trai-subs").state.value)
    trace.append([e.model_dump(mode="json") for e in repo.list_audit()][:0])
    # Audit: action, actor, entity (ids and times are random per run).
    trace.append([(e.actor, e.action, e.entity) for e in reversed(repo.list_audit())][-12:])
    trace.append(dump(repo.list_decisions("item-trai-subs")))
    return trace


def test_the_same_changes_give_the_same_result_on_both_backends(both) -> None:
    memory, sql = both
    a, b = run_script(memory), run_script(sql)
    # decided_at of decisions is fixed in the script, so those compare exactly too
    assert a == b


# ---------- crawler writes (Prompt 9) ----------

SEEDED_SOURCE = "rbi"  # a source id in the demo seed
PAGE = b"<html><body>RBI notifications listing</body></html>"


def crawl_snapshot(content: bytes = PAGE, snap_id: str = "snap-crawl-1") -> Snapshot:
    digest = hashlib.sha256(content).hexdigest()
    source = SEEDED_SOURCE
    return Snapshot(
        id=snap_id,
        source_id=source,
        channel=CheckChannel.HTML,
        url="https://rbi.org.in/notifications",
        fetched_at=now_ist(),
        sha256=digest,
        path=f"data/snapshots/{source}/2026-11-23/{digest}",
    )


def crawl_run(repo):
    return repo.create_run(Cycle.ADHOC, date(2026, 11, 23), "tester", "tester")


def test_record_snapshot_stores_row_file_and_audit_event(repo) -> None:
    before = len(repo.list_audit())
    repo.record_snapshot(crawl_snapshot(), PAGE, "crawler")
    assert repo.get_snapshot("snap-crawl-1").sha256 == hashlib.sha256(PAGE).hexdigest()
    assert repo.read_blob(hashlib.sha256(PAGE).hexdigest()) == PAGE
    assert len(repo.list_audit()) == before + 1
    assert repo.list_audit()[0].action == "snapshot.recorded"


def test_record_snapshot_refuses_content_that_does_not_match_the_hash(repo) -> None:
    before = len(repo.list_audit())
    with pytest.raises(ValueError):
        repo.record_snapshot(crawl_snapshot(), b"something else", "crawler")
    assert repo.get_snapshot("snap-crawl-1") is None
    assert len(repo.list_audit()) == before


def test_a_snapshot_cannot_be_recorded_twice_under_one_id(repo) -> None:
    repo.record_snapshot(crawl_snapshot(), PAGE, "crawler")
    with pytest.raises(Exception):  # noqa: B017 - the two backends raise different error types
        repo.record_snapshot(crawl_snapshot(), PAGE, "crawler")


def test_raw_listing_items_are_stored_per_run_and_source(repo) -> None:
    run = crawl_run(repo)
    repo.record_snapshot(crawl_snapshot(), PAGE, "crawler")
    items = [
        RawListingItem(
            id=f"raw-{n}",
            run_id=run.id,
            source_id=SEEDED_SOURCE,
            channel=CheckChannel.HTML,
            snapshot_id="snap-crawl-1",
            title=f"Notification {n}",
            url=f"https://rbi.org.in/n/{n}",
            ref_no=f"RBI/2026-27/{n}",
            displayed_date=date(2026, 11, 20),
            raw_extra={"n": n},
            fetched_at=now_ist(),
        )
        for n in (1, 2)
    ]
    before = len(repo.list_audit())
    repo.add_raw_items(items, "crawler")
    assert [i.id for i in repo.list_raw_items(run.id)] == ["raw-1", "raw-2"]
    assert repo.list_raw_items(run.id, "some-other-source") == []
    assert repo.list_raw_items("no-such-run") == []
    assert len(repo.list_audit()) == before + 1  # one audit event for the batch


def test_the_database_refuses_to_change_or_delete_a_snapshot(tmp_path, sql_template) -> None:
    repository, url, _files = make_backend("sql", tmp_path, sql_template)
    repository.record_snapshot(crawl_snapshot(), PAGE, "crawler")
    con = sqlite3.connect(url.removeprefix("sqlite:///"))
    try:
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("UPDATE snapshot SET sha256 = 'x' WHERE id = 'snap-crawl-1'")
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("DELETE FROM snapshot WHERE id = 'snap-crawl-1'")
    finally:
        con.close()
