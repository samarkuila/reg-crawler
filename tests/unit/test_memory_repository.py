"""Contract checks for the in-memory repository (the SQL repository must pass the same ones)."""

import inspect
import re
from datetime import date

import pytest

from app.repositories import seed
from app.repositories.base import Repository
from app.repositories.factory import make_repository
from app.repositories.memory import MemoryRepository
from app.schemas.api import CycleRef, ItemFilter
from app.schemas.entities import ReviewDecision
from app.schemas.enums import ItemState, Treatment

# Every method that changes state. The list is explicit so a new mutating method is noticed.
MUTATING = {
    "create_source",
    "add_source_version",
    "approve_source_version",
    "reject_source_version",
    "create_run",
    "record_source_check",
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


@pytest.fixture
def repo() -> MemoryRepository:
    return make_repository("memory")  # type: ignore[return-value]


def protocol_methods() -> dict[str, inspect.Signature]:
    return {
        name: inspect.signature(fn)
        for name, fn in inspect.getmembers(Repository, inspect.isfunction)
        if not name.startswith("_")
    }


def test_every_mutating_method_takes_an_actor() -> None:
    with_actor = {n for n, sig in protocol_methods().items() if "actor" in sig.parameters}
    # list_audit filters by actor; it only reads.
    assert with_actor - {"list_audit"} == MUTATING


def test_memory_repository_implements_the_whole_protocol(repo) -> None:
    for name, sig in protocol_methods().items():
        impl = getattr(repo, name, None)
        assert impl is not None, f"MemoryRepository.{name} missing"
        assert list(inspect.signature(impl).parameters) == [
            p for p in sig.parameters if p != "self"
        ], name


def test_evidence_tables_are_append_only() -> None:
    names = set(protocol_methods())
    assert not [n for n in names if re.match(r"(delete|remove|update|edit)_", n)]
    assert "list_audit" in names and not [n for n in names if "audit" in n and n != "list_audit"]


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
    decision = ReviewDecision(
        id="dec-test",
        item_id="item-trai-subs",
        reviewer="Divya G.",
        decided_at=seed.ist(21, 11, 9, 0),
        treatment=Treatment.NOT_RELEVANT,
        dismiss_reason="x",
    )
    calls = {"n": 0}
    real = repo._audit

    def flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:  # fail on the state-change event, after the decision was stored
            raise RuntimeError("disk full")
        return real(*args, **kwargs)

    monkeypatch.setattr(repo, "_audit", flaky)
    with pytest.raises(RuntimeError):
        repo.save_decisions([decision], ItemState.DECIDED, "Divya G.")
    assert repo.list_decisions("item-trai-subs") == []
    assert repo.get_item("item-trai-subs").state == ItemState.DETECTED
    assert len(repo.list_audit()) == n_audit


def test_mutation_audit_event_is_written_with_the_actor(repo) -> None:
    check = repo.add_manual_check("chk-2026-11-20-eod-egazette-html", "Divya G.", "ok", "Divya G.")
    event = repo.list_audit()[0]
    assert (event.actor, event.action, event.entity) == (
        "Divya G.",
        "manual_check.added",
        "manual_check",
    )
    assert event.entity_id == check.id and event.after["note"] == "ok" and event.before is None


def test_factory_backends(monkeypatch) -> None:
    assert isinstance(make_repository("memory"), MemoryRepository)
    monkeypatch.setenv("REPO_BACKEND", "memory")
    assert isinstance(make_repository(), MemoryRepository)
    with pytest.raises(NotImplementedError):
        make_repository("sql")
    monkeypatch.setenv("REPO_BACKEND", "sql")
    with pytest.raises(NotImplementedError):
        make_repository()
    with pytest.raises(ValueError):
        make_repository("mongo")


def test_each_factory_call_returns_independent_state() -> None:
    a, b = make_repository("memory"), make_repository("memory")
    a.add_manual_check("chk-2026-11-20-eod-egazette-html", "Divya G.", "ok", "Divya G.")
    assert b.get_manual_check("chk-2026-11-20-eod-egazette-html") is None


# --- seed data matches the mockups -------------------------------------------------------------


def test_seed_sources(repo) -> None:
    sources = repo.list_sources()
    assert len([s for s in sources if s.active]) == 5
    planned = [s for s in sources if not s.active]
    assert len(planned) == 8 and all(s.planned and s.current_version == 0 for s in planned)


def test_seed_run_and_blocked_check(repo) -> None:
    ref = CycleRef(cycle_date=date(2026, 11, 20), cycle="EOD")
    (run,) = repo.find_runs(ref)
    checks = repo.list_source_checks(run.id)
    assert [c.source_id for c in checks if c.status.value == "BLOCKED"] == ["egazette"]
    assert sum(1 for c in checks if c.status.value == "OK") == 5


def test_seed_users(repo) -> None:
    roles = {u.name: u.role.value for u in repo.list_users()}
    assert roles == {
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


def test_fingerprint_rule_uses_ref_no_else_url_never_the_date() -> None:
    with_ref = seed.fingerprint("rbi", "RBI/2026-27/118", "https://x/1")
    assert with_ref == seed.fingerprint("rbi", "RBI/2026-27/118", "https://x/2")  # url ignored
    assert seed.fingerprint("rbi", None, "https://x/1") != seed.fingerprint(
        "rbi", None, "https://x/2"
    )
    assert with_ref != seed.fingerprint("trai", "RBI/2026-27/118", "https://x/1")


def test_snapshots_have_sha256_and_documents_are_hashed(repo) -> None:
    for obs in repo.list_observations("item-iors-faqs"):
        if obs.snapshot_id:
            assert re.fullmatch(r"[0-9a-f]{64}", repo.get_snapshot(obs.snapshot_id).sha256)
    doc = repo.list_documents("item-iors-faqs")[0]
    assert re.fullmatch(r"[0-9a-f]{64}", doc.sha256)
