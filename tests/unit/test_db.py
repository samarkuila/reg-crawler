"""Database schema (Prompt 7): fresh migration, architecture §4 coverage, immutability, indexes,
reference numbers. Uses temporary SQLite files only (no network, nothing under data/)."""

import re
import threading
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import IntegrityError, StatementError

from app.db import er, models
from app.db.models import Base
from app.db.refs import format_ref, next_internal_ref
from app.db.session import make_engine, make_session_factory, session_scope
from app.schemas import entities

ROOT = Path(__file__).resolve().parents[2]
IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 20, 19, 30, tzinfo=IST)


def alembic_config(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture
def url(tmp_path: Path) -> str:
    return f"sqlite:///{(tmp_path / 'test.db').as_posix()}"


@pytest.fixture
def engine(url: str) -> Engine:
    command.upgrade(alembic_config(url), "head")
    eng = make_engine(url)
    yield eng
    eng.dispose()


@pytest.fixture
def factory(engine: Engine):
    return make_session_factory(engine)


# Columns exactly as listed in docs/architecture.md section 4 (the "Key columns" table).
ARCHITECTURE = {
    "source": "id authority page_name type url rss_url adapter_config include_keywords "
    "exclude_keywords priority requires_js active current_version",
    "source_version": "id source_id version before after status requested_by approved_by ts",
    "crawl_run": "id cycle_date cycle triggered_by started_at ended_at status",
    "source_check": "id run_id source_id channel status http_status items_found error_detail "
    "snapshot_id",
    "snapshot": "id source_id channel url fetched_at sha256 path",
    "regulatory_item": "id internal_ref source_id fingerprint canonical_url title ref_no "
    "displayed_date channel channel_mismatch relevance_score relevance_reason priority state "
    "first_seen_run_id first_seen_at last_seen_at",
    "item_observation": "id item_id run_id channel change_type prior_hash current_hash snapshot_id",
    "document": "id item_id url sha256 path mime text_path fetch_status uploaded_by fetched_at",
    "review_decision": "id item_id reviewer decided_at treatment include_in_update applicability "
    "ref_no_and_date authority guideline_title issue_date publish_date effective_date "
    "status_colour impact_text sme_function dismiss_reason fields_edited",
    "daily_update": "id cycle_date cycle status subject html sources_ok sources_failed "
    "approved_by approved_at",
    "daily_update_item": "update_id item_id decision_id serial_no",
    "manual_check": "id source_check_id user note ts",
    "app_user": "id name email role",
    "audit_event": "id ts actor action entity entity_id before after",
}
# "Additions made in M1" (architecture section 4), which the SQL models must carry.
M1_ADDITIONS = {
    "source": {"planned"},
    "source_version": {"decided_at", "reject_reason"},
    "regulatory_item": {"summary"},
    "document": {"filename"},
    "review_decision": {"is_draft"},
    "daily_update": {"prepared_by", "exported_at"},
}
# Versioned configuration: the doc lists the tables without columns.
CONFIG_TABLES = {
    "sme_function": "id name head_name head_email active version",
    "recipient": "id name email kind active version",
    "email_template": "id name subject_pattern greeting intro disclaimer no_update_text sign_off "
    "sender_name version",
    "setting": "key value version description updated_by updated_at pending",
}
EXTRA_TABLES = {"internal_ref_counter"}


def columns(engine: Engine, table: str) -> set[str]:
    return {c["name"] for c in inspect(engine).get_columns(table)}


# ---------- schema ----------


def test_migrate_builds_a_fresh_database_with_every_architecture_table(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    assert tables == set(ARCHITECTURE) | set(CONFIG_TABLES) | EXTRA_TABLES


def test_every_architecture_column_exists_and_extras_are_the_documented_additions(
    engine: Engine,
) -> None:
    for table, listed in ARCHITECTURE.items():
        have = columns(engine, table)
        want = set(listed.split())
        assert want <= have, (table, want - have)
        assert have - want == M1_ADDITIONS.get(table, set()), (table, have - want)
    for table, listed in CONFIG_TABLES.items():
        assert columns(engine, table) == set(listed.split()), table


def test_database_matches_the_models_exactly(engine: Engine) -> None:
    with engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata
        )
    assert diff == []


def test_models_cover_every_field_of_the_application_entities(engine: Engine) -> None:
    pairs = {
        entities.Source: "source",
        entities.SourceVersion: "source_version",
        entities.CrawlRun: "crawl_run",
        entities.SourceCheck: "source_check",
        entities.Snapshot: "snapshot",
        entities.RegulatoryItem: "regulatory_item",
        entities.ItemObservation: "item_observation",
        entities.Document: "document",
        entities.ReviewDecision: "review_decision",
        entities.DailyUpdate: "daily_update",
        entities.DailyUpdateItem: "daily_update_item",
        entities.ManualCheck: "manual_check",
        entities.SmeFunction: "sme_function",
        entities.Recipient: "recipient",
        entities.EmailTemplate: "email_template",
        entities.Setting: "setting",
        entities.AppUser: "app_user",
        entities.AuditEvent: "audit_event",
    }
    for entity, table in pairs.items():
        missing = set(entity.model_fields) - columns(engine, table)
        assert not missing, (table, missing)


def test_indexes_and_uniqueness_from_the_brief(engine: Engine) -> None:
    insp = inspect(engine)
    item_indexes = {i["name"]: i for i in insp.get_indexes("regulatory_item")}
    uniques = {tuple(u["column_names"]) for u in insp.get_unique_constraints("regulatory_item")}
    assert ("fingerprint",) in uniques and ("internal_ref",) in uniques
    assert item_indexes["ix_regulatory_item_source_id_first_seen_at"]["column_names"] == [
        "source_id",
        "first_seen_at",
    ]
    assert item_indexes["ix_regulatory_item_state"]["column_names"] == ["state"]
    assert {i["name"] for i in insp.get_indexes("crawl_run")} == {"ix_crawl_run_cycle_date"}


def test_migration_can_go_down_and_up_again(url: str) -> None:
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    eng = make_engine(url)
    assert set(inspect(eng).get_table_names()) == {"alembic_version"}
    with eng.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM sqlite_master WHERE type='trigger'")).scalar()
            == 0
        )
    eng.dispose()
    command.upgrade(cfg, "head")
    eng = make_engine(url)
    assert "audit_event" in inspect(eng).get_table_names()
    eng.dispose()


# ---------- helpers that create rows ----------


def add_parents(session) -> dict[str, str]:
    session.add(
        models.Source(
            id="rbi",
            authority="RBI",
            page_name="Notifications",
            type="RSS",
            url="https://rbi.org.in/",
        )
    )
    session.add(
        models.CrawlRun(
            id="run-1",
            cycle_date=date(2026, 11, 20),
            cycle="EOD",
            triggered_by="scheduler",
            started_at=NOW,
        )
    )
    session.flush()
    return {"source": "rbi", "run": "run-1"}


def add_item(session, ref: str = "TVSC-REG-2026-000001", fingerprint: str = "f" * 64) -> str:
    item = models.RegulatoryItem(
        internal_ref=ref,
        source_id="rbi",
        fingerprint=fingerprint,
        canonical_url="https://x/1",
        title="Circular",
        channel="RSS",
        first_seen_run_id="run-1",
        first_seen_at=NOW,
        last_seen_at=NOW,
    )
    session.add(item)
    session.flush()
    return item.id


def add_evidence(session) -> dict[str, str]:
    """One row in each append-only table."""
    add_parents(session)
    item_id = add_item(session)
    snap = models.Snapshot(
        source_id="rbi",
        channel="RSS",
        url="https://rbi.org.in/rss",
        fetched_at=NOW,
        sha256="a" * 64,
        path="data/snapshots/rbi/2026-11-20/aaaa",
    )
    doc = models.Document(
        item_id=item_id,
        url="https://x/1.pdf",
        sha256="b" * 64,
        path="data/documents/x",
        fetch_status="OK",
    )
    audit = models.AuditEvent(
        ts=NOW,
        actor="Divya G.",
        action="review_decision.accepted",
        entity="review_decision",
        entity_id="dec-1",
        before=None,
        after={"include": True},
    )
    session.add_all([snap, doc, audit])
    session.flush()
    return {"snapshot": snap.id, "document": doc.id, "audit_event": audit.id}


# ---------- immutability ----------


@pytest.mark.parametrize("table", ["snapshot", "document", "audit_event"])
def test_append_only_rows_cannot_be_updated_or_deleted(factory, engine: Engine, table: str) -> None:
    with session_scope(factory) as session:
        ids = add_evidence(session)
    row_id = ids[table]
    column = {"snapshot": "url", "document": "url", "audit_event": "actor"}[table]
    for sql in (
        f"UPDATE {table} SET {column} = 'changed' WHERE id = :id",
        f"UPDATE {table} SET {column} = {column}",  # even a no-op rewrite is refused
        f"DELETE FROM {table} WHERE id = :id",
        f"DELETE FROM {table}",
    ):
        with pytest.raises(IntegrityError, match="append-only"), engine.begin() as conn:
            conn.execute(text(sql), {"id": row_id})
    with engine.connect() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {table}")).scalar() == 1
        assert conn.execute(text(f"SELECT {column} FROM {table}")).scalar() != "changed"


def test_orm_changes_to_append_only_rows_are_refused_too(factory) -> None:
    with session_scope(factory) as session:
        ids = add_evidence(session)
    with pytest.raises(IntegrityError, match="append-only"), session_scope(factory) as session:
        session.get(models.AuditEvent, ids["audit_event"]).actor = "someone else"
    with pytest.raises(IntegrityError, match="append-only"), session_scope(factory) as session:
        session.delete(session.get(models.Snapshot, ids["snapshot"]))
    with pytest.raises(IntegrityError, match="append-only"), session_scope(factory) as session:
        session.delete(session.get(models.Document, ids["document"]))


def test_new_evidence_can_still_be_appended(factory) -> None:
    with session_scope(factory) as session:
        add_evidence(session)
    with session_scope(factory) as session:
        session.add(
            models.AuditEvent(
                ts=NOW,
                actor="George K.",
                action="x",
                entity="e",
                entity_id="1",
                before=None,
                after=None,
            )
        )
    with session_scope(factory) as session:
        assert session.query(models.AuditEvent).count() == 2


def test_other_tables_stay_editable(factory) -> None:
    with session_scope(factory) as session:
        add_parents(session)
        item_id = add_item(session)
    with session_scope(factory) as session:
        item = session.get(models.RegulatoryItem, item_id)
        item.state = "IN_REVIEW"
    with session_scope(factory) as session:
        assert session.get(models.RegulatoryItem, item_id).state.value == "IN_REVIEW"


# ---------- rules and values ----------


def test_fingerprint_and_reference_must_be_unique(factory) -> None:
    with session_scope(factory) as session:
        add_parents(session)
        add_item(session)
    with pytest.raises(IntegrityError), session_scope(factory) as session:
        add_item(session, ref="TVSC-REG-2026-000002")  # same fingerprint
    with pytest.raises(IntegrityError), session_scope(factory) as session:
        add_item(session, fingerprint="e" * 64)  # same internal_ref


def test_foreign_keys_and_enum_values_are_enforced(factory, engine: Engine) -> None:
    with pytest.raises(IntegrityError), session_scope(factory) as session:
        add_item(session)  # no source or run yet
    with pytest.raises(IntegrityError), engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO crawl_run (id, cycle_date, cycle, triggered_by, started_at, status) "
                "VALUES ('r', '2026-11-20', 'NOON', 'x', '2026-11-20 00:00:00', 'RUNNING')"
            )
        )


def test_json_dates_and_timezones_round_trip(factory) -> None:
    with session_scope(factory) as session:
        add_parents(session)
        session.add(
            models.Setting(
                key="run-times", value={"morning": "10:00"}, updated_at=NOW, pending=None
            )
        )
        session.add(
            models.ReviewDecision(
                id="d1",
                item_id=add_item(session),
                reviewer="Divya G.",
                decided_at=NOW,
                fields_edited={"authority": {"prefilled": "RBI", "final": "RBI (x)"}},
                effective_date=date(2026, 11, 13),
            )
        )
    with session_scope(factory) as session:
        setting = session.get(models.Setting, "run-times")
        assert setting.value == {"morning": "10:00"} and setting.pending is None
        decision = session.get(models.ReviewDecision, "d1")
        assert decision.fields_edited["authority"]["final"] == "RBI (x)"
        assert decision.effective_date == date(2026, 11, 13)
        assert decision.decided_at == NOW and decision.decided_at.utcoffset() == timedelta(0)
        run = session.get(models.CrawlRun, "run-1")
        assert run.started_at == NOW and run.ended_at is None


def test_a_datetime_without_a_timezone_is_refused(factory) -> None:
    with pytest.raises(StatementError, match="timezone"), session_scope(factory) as session:
        session.add(
            models.CrawlRun(
                cycle_date=date(2026, 11, 20),
                cycle="EOD",
                triggered_by="x",
                started_at=datetime(2026, 11, 20, 19, 30),
            )
        )


def test_new_rows_get_uuid_keys(factory) -> None:
    with session_scope(factory) as session:
        add_parents(session)
        item_id = add_item(session)
    assert re.fullmatch(r"[0-9a-f-]{36}", item_id)


# ---------- internal reference numbers ----------


def test_internal_refs_count_up_per_year_and_roll_back_cleanly(factory) -> None:
    with session_scope(factory) as session:
        assert next_internal_ref(session, 2026) == "TVSC-REG-2026-000001"
        assert next_internal_ref(session, 2026) == "TVSC-REG-2026-000002"
        assert next_internal_ref(session, 2027) == "TVSC-REG-2027-000001"
    with pytest.raises(RuntimeError), session_scope(factory) as session:
        next_internal_ref(session, 2026)
        raise RuntimeError("item could not be saved")
    with session_scope(factory) as session:
        assert next_internal_ref(session, 2026) == "TVSC-REG-2026-000003"  # no gap
    assert format_ref(2026, 214) == "TVSC-REG-2026-000214"


def test_internal_refs_are_unique_and_gap_free_under_concurrency(factory) -> None:
    results: list[str] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            for _ in range(15):
                with session_scope(factory) as session:
                    ref = next_internal_ref(
                        session, 2026
                    )  # first call also races to create the row
                with lock:
                    results.append(ref)
        except BaseException as exc:  # noqa: BLE001 - reported below
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert len(results) == 120 and len(set(results)) == 120
    assert sorted(results) == [format_ref(2026, n) for n in range(1, 121)]


# ---------- documentation stays in step with the code ----------


def test_er_diagram_in_the_doc_is_generated_from_the_models() -> None:
    assert er.current_block() == f"```mermaid\n{er.mermaid()}\n```", (
        "Run: python -m app.db.er --write"
    )
    for table in Base.metadata.tables:
        assert table.upper() in er.mermaid()


def test_postgres_doc_has_the_trigger_sql_for_every_protected_table() -> None:
    doc = (ROOT / "docs" / "postgres-migration.md").read_text(encoding="utf-8")
    assert "reject_append_only_change" in doc
    for table in models.APPEND_ONLY_TABLES:
        for action in ("update", "delete", "truncate"):
            assert f"trg_{table}_no_{action}" in doc, (table, action)


def test_db_layer_does_not_depend_on_services_or_api() -> None:
    for path in (ROOT / "app" / "db").rglob("*.py"):
        text_ = path.read_text(encoding="utf-8")
        assert not re.search(
            r"^\s*(from|import)\s+app\.(services|api|repositories)", text_, re.M
        ), path


def test_utc_helper_is_timezone_aware() -> None:
    assert datetime.now(UTC).tzinfo is not None
