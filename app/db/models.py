"""SQLAlchemy 2.0 models for every table in docs/architecture.md §4.

Portable on purpose: string UUID keys, generic JSON, CHECK-constrained text for enums, UTC
datetimes. Nothing here is specific to SQLite. `snapshot`, `document` and `audit_event` are
append-only; the database refuses UPDATE and DELETE on them (see the first migration).

Columns marked "(added)" were added in M1 (architecture §4 "Additions made in M1").
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.db.types import TZDateTime, enum_column, new_id
from app.schemas.enums import (
    AgentStatus,
    AgentTask,
    AlertKind,
    Applicability,
    BaselineStatus,
    ChangeType,
    Channel,
    CheckChannel,
    CheckStatus,
    Cycle,
    FetchEngine,
    FetchStatus,
    FirstRunMode,
    FlagStatus,
    FlagType,
    ItemOrigin,
    ItemState,
    NbfcRelevance,
    Priority,
    RecipientKind,
    RunStatus,
    SeenReason,
    SourceType,
    StatusColour,
    Treatment,
    UpdateStatus,
    UserRole,
    VersionStatus,
)

# Constraint names are explicit so migrations and a future PostgreSQL move stay predictable.
NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

ID = String(36)
SHA = String(64)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


def _id() -> Mapped[str]:
    return mapped_column(ID, primary_key=True, default=new_id)


# --- Sources ---------------------------------------------------------------------------------


class Source(Base):
    __tablename__ = "source"

    id: Mapped[str] = _id()
    authority: Mapped[str] = mapped_column(String(100))
    page_name: Mapped[str] = mapped_column(String(200))
    type: Mapped[SourceType] = mapped_column(enum_column(SourceType, "source_type"))
    url: Mapped[str] = mapped_column(Text)
    rss_url: Mapped[str | None] = mapped_column(Text)
    adapter_config: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    include_keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    exclude_keywords: Mapped[list[str]] = mapped_column(JSON, default=list)
    priority: Mapped[Priority] = mapped_column(
        enum_column(Priority, "source_priority"), default=Priority.NORMAL
    )
    # Deprecated (M5): replaced by fetch_engine. The column stays (NOT NULL) and is always False.
    requires_js: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    fetch_engine: Mapped[FetchEngine] = mapped_column(
        enum_column(FetchEngine, "source_fetch_engine"),
        default=FetchEngine.HTTP,
        server_default="http",
    )
    wait_for_selector: Mapped[str | None] = mapped_column(Text, nullable=True)
    wait_timeout_s: Mapped[int] = mapped_column(Integer, default=15, server_default="15")
    active: Mapped[bool] = mapped_column(Boolean, default=False)
    current_version: Mapped[int] = mapped_column(Integer, default=0)
    planned: Mapped[bool] = mapped_column(Boolean, default=False)  # (added)
    retired: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # (added)
    # Register order (creation order). The SQL repository lists sources by this.
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")  # (added)
    # First-crawl window and baseline (added 24.11.2026)
    first_run_mode: Mapped[FirstRunMode | None] = mapped_column(
        enum_column(FirstRunMode, "source_first_run_mode"), nullable=True
    )
    first_run_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sorted_newest_first: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    baseline_status: Mapped[BaselineStatus] = mapped_column(
        enum_column(BaselineStatus, "source_baseline_status"),
        default=BaselineStatus.NOT_BASELINED,
        server_default="NOT_BASELINED",
    )
    baseline_at: Mapped[datetime | None] = mapped_column(TZDateTime, nullable=True)
    baseline_window_start: Mapped[date | None] = mapped_column(Date, nullable=True)


class SourceVersion(Base):
    __tablename__ = "source_version"
    __table_args__ = (UniqueConstraint("source_id", "version"),)

    id: Mapped[str] = _id()
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id"), index=True)
    version: Mapped[int] = mapped_column(Integer)
    before: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    after: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[VersionStatus] = mapped_column(
        enum_column(VersionStatus, "source_version_status"), default=VersionStatus.PENDING
    )
    requested_by: Mapped[str] = mapped_column(String(100))
    approved_by: Mapped[str | None] = mapped_column(String(100))
    ts: Mapped[datetime] = mapped_column(TZDateTime)
    decided_at: Mapped[datetime | None] = mapped_column(TZDateTime)  # (added)
    reject_reason: Mapped[str | None] = mapped_column(Text)  # (added)


# --- Runs and evidence -----------------------------------------------------------------------


class CrawlRun(Base):
    __tablename__ = "crawl_run"
    __table_args__ = (Index("ix_crawl_run_cycle_date", "cycle_date"),)

    id: Mapped[str] = _id()
    cycle_date: Mapped[date] = mapped_column(Date)
    cycle: Mapped[Cycle] = mapped_column(enum_column(Cycle, "crawl_run_cycle"))
    triggered_by: Mapped[str] = mapped_column(String(100))
    started_at: Mapped[datetime] = mapped_column(TZDateTime)
    ended_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    status: Mapped[RunStatus] = mapped_column(
        enum_column(RunStatus, "crawl_run_status"), default=RunStatus.RUNNING
    )
    sources: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)  # None = all sources


class Snapshot(Base):
    """Append-only: the database rejects UPDATE and DELETE."""

    __tablename__ = "snapshot"

    id: Mapped[str] = _id()
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id"), index=True)
    channel: Mapped[CheckChannel] = mapped_column(enum_column(CheckChannel, "snapshot_channel"))
    url: Mapped[str] = mapped_column(Text)
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime)
    sha256: Mapped[str] = mapped_column(SHA, index=True)
    path: Mapped[str] = mapped_column(Text)


class SourceCheck(Base):
    __tablename__ = "source_check"
    __table_args__ = (UniqueConstraint("run_id", "source_id", "channel"),)

    id: Mapped[str] = _id()
    run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id"), index=True)
    channel: Mapped[CheckChannel] = mapped_column(enum_column(CheckChannel, "source_check_channel"))
    status: Mapped[CheckStatus] = mapped_column(enum_column(CheckStatus, "source_check_status"))
    http_status: Mapped[int | None] = mapped_column(Integer)
    items_found: Mapped[int | None] = mapped_column(Integer)
    error_detail: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshot.id"))
    stats: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)  # (added) run counters


class Alert(Base):
    """(added, Prompt 13) Post-run alerts: shown as a banner, kept for V2 notifications."""

    __tablename__ = "alert"
    __table_args__ = (Index("ix_alert_run_id", "run_id"),)

    id: Mapped[str] = _id()
    run_id: Mapped[str | None] = mapped_column(ForeignKey("crawl_run.id"))
    kind: Mapped[AlertKind] = mapped_column(enum_column(AlertKind, "alert_kind"))
    severity: Mapped[str] = mapped_column(String(10), default="warning")
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source.id"))
    item_id: Mapped[str | None] = mapped_column(ForeignKey("regulatory_item.id"))
    message: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)
    delivered_at: Mapped[datetime | None] = mapped_column(TZDateTime)


class AiExtraction(Base):
    """Append-only (database trigger): Luna's suggestions for an item."""

    __tablename__ = "ai_extraction"

    id: Mapped[str] = _id()
    item_id: Mapped[str] = mapped_column(ForeignKey("regulatory_item.id"), index=True)
    task: Mapped[AgentTask] = mapped_column(enum_column(AgentTask, "ai_extraction_task"))
    fields: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    nbfc_relevance: Mapped[NbfcRelevance | None] = mapped_column(
        enum_column(NbfcRelevance, "ai_extraction_relevance"), nullable=True
    )
    relevance_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    suggested_treatment: Mapped[str | None] = mapped_column(String(40))
    suggested_sme_function: Mapped[str | None] = mapped_column(String(60))
    model: Mapped[str] = mapped_column(String(80))
    deployment: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(40))
    guidance_version: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)


class AgentFlag(Base):
    __tablename__ = "agent_flag"

    id: Mapped[str] = _id()
    run_id: Mapped[str | None] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    source_id: Mapped[str | None] = mapped_column(ForeignKey("source.id"), index=True)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshot.id"))
    item_id: Mapped[str | None] = mapped_column(ForeignKey("regulatory_item.id"))
    type: Mapped[FlagType] = mapped_column(enum_column(FlagType, "agent_flag_type"))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[FlagStatus] = mapped_column(
        enum_column(FlagStatus, "agent_flag_status"), default=FlagStatus.OPEN
    )
    resolved_by: Mapped[str | None] = mapped_column(String(100))
    resolved_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    created_at: Mapped[datetime] = mapped_column(TZDateTime)


class AgentRun(Base):
    __tablename__ = "agent_run"

    id: Mapped[str] = _id()
    run_id: Mapped[str | None] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    task: Mapped[AgentTask] = mapped_column(enum_column(AgentTask, "agent_run_task"))
    item_id: Mapped[str | None] = mapped_column(ForeignKey("regulatory_item.id"))
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshot.id"))
    model: Mapped[str] = mapped_column(String(80))
    deployment: Mapped[str | None] = mapped_column(String(120))
    prompt_version: Mapped[str] = mapped_column(String(40))
    guidance_version: Mapped[int | None] = mapped_column(Integer)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    cost_inr: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[AgentStatus] = mapped_column(enum_column(AgentStatus, "agent_run_status"))
    detail: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime, index=True)


class RawListingItem(Base):
    """(added, Prompt 9) Listing rows as read by a spider, before detection decides anything."""

    __tablename__ = "raw_listing_item"
    __table_args__ = (Index("ix_raw_listing_item_run_source", "run_id", "source_id"),)

    id: Mapped[str] = _id()
    run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"))
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id"))
    channel: Mapped[CheckChannel] = mapped_column(
        enum_column(CheckChannel, "raw_listing_item_channel")
    )
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshot.id"))
    title: Mapped[str] = mapped_column(Text)
    url: Mapped[str] = mapped_column(Text)
    ref_no: Mapped[str | None] = mapped_column(String(200))
    displayed_date: Mapped[date | None] = mapped_column(Date)
    # Plain text of the row's description / paragraph (HTML stripped); None if the row had none.
    description_text: Mapped[str | None] = mapped_column(Text)
    raw_extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    fetched_at: Mapped[datetime] = mapped_column(TZDateTime)


class ManualCheck(Base):
    __tablename__ = "manual_check"

    id: Mapped[str] = _id()
    # One acknowledgement per check: a check is either still open or checked by a named person.
    source_check_id: Mapped[str] = mapped_column(ForeignKey("source_check.id"), unique=True)
    user: Mapped[str] = mapped_column(String(100))
    note: Mapped[str] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(TZDateTime)


# --- Items -----------------------------------------------------------------------------------


class RegulatoryItem(Base):
    __tablename__ = "regulatory_item"
    __table_args__ = (
        Index("ix_regulatory_item_source_id_first_seen_at", "source_id", "first_seen_at"),
        Index("ix_regulatory_item_state", "state"),
    )

    id: Mapped[str] = _id()
    internal_ref: Mapped[str] = mapped_column(String(24), unique=True)  # TVSC-REG-YYYY-NNNNNN
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id"))
    fingerprint: Mapped[str] = mapped_column(SHA, unique=True)
    canonical_url: Mapped[str] = mapped_column(Text)
    title: Mapped[str] = mapped_column(Text)
    ref_no: Mapped[str | None] = mapped_column(String(200))
    displayed_date: Mapped[date | None] = mapped_column(Date)
    channel: Mapped[Channel] = mapped_column(enum_column(Channel, "regulatory_item_channel"))
    channel_mismatch: Mapped[bool] = mapped_column(Boolean, default=False)
    relevance_score: Mapped[int] = mapped_column(Integer, default=0)
    relevance_reason: Mapped[str | None] = mapped_column(Text)
    priority: Mapped[Priority] = mapped_column(
        enum_column(Priority, "regulatory_item_priority"), default=Priority.NORMAL
    )
    state: Mapped[ItemState] = mapped_column(
        enum_column(ItemState, "regulatory_item_state"), default=ItemState.DETECTED
    )
    first_seen_run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"))
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime)
    last_seen_at: Mapped[datetime] = mapped_column(TZDateTime)
    summary: Mapped[str | None] = mapped_column(Text)  # (added)
    origin: Mapped[ItemOrigin] = mapped_column(
        enum_column(ItemOrigin, "regulatory_item_origin"),
        default=ItemOrigin.CRAWL,
        server_default="CRAWL",
    )
    flags: Mapped[list[str]] = mapped_column(JSON, default=list)


class SeenFingerprint(Base):
    """(added 24.11.2026) Insert-only: items the first crawl saw but did not store."""

    __tablename__ = "seen_fingerprint"
    __table_args__ = (UniqueConstraint("source_id", "fingerprint"),)

    id: Mapped[str] = _id()
    source_id: Mapped[str] = mapped_column(ForeignKey("source.id"), index=True)
    fingerprint: Mapped[str] = mapped_column(SHA)
    title: Mapped[str] = mapped_column(Text)
    published_date: Mapped[date | None] = mapped_column(Date)
    first_seen_run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"))
    first_seen_at: Mapped[datetime] = mapped_column(TZDateTime)
    reason: Mapped[SeenReason] = mapped_column(enum_column(SeenReason, "seen_fingerprint_reason"))


class InternalRefCounter(Base):
    """One row per year. `next_internal_ref` increments it atomically (see app/db/refs.py)."""

    __tablename__ = "internal_ref_counter"

    year: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    last_value: Mapped[int] = mapped_column(Integer, default=0)


class ItemObservation(Base):
    __tablename__ = "item_observation"

    id: Mapped[str] = _id()
    item_id: Mapped[str] = mapped_column(ForeignKey("regulatory_item.id"), index=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("crawl_run.id"), index=True)
    channel: Mapped[CheckChannel] = mapped_column(
        enum_column(CheckChannel, "item_observation_channel")
    )
    change_type: Mapped[ChangeType] = mapped_column(
        enum_column(ChangeType, "item_observation_change_type")
    )
    prior_hash: Mapped[str | None] = mapped_column(SHA)
    current_hash: Mapped[str] = mapped_column(SHA)
    snapshot_id: Mapped[str | None] = mapped_column(ForeignKey("snapshot.id"))


class Document(Base):
    """Append-only: the database rejects UPDATE and DELETE."""

    __tablename__ = "document"

    id: Mapped[str] = _id()
    item_id: Mapped[str] = mapped_column(ForeignKey("regulatory_item.id"), index=True)
    url: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(SHA)  # empty while the download is blocked
    path: Mapped[str | None] = mapped_column(Text)
    mime: Mapped[str | None] = mapped_column(String(100))
    text_path: Mapped[str | None] = mapped_column(Text)
    needs_ocr: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    fetch_status: Mapped[FetchStatus] = mapped_column(enum_column(FetchStatus, "document_status"))
    uploaded_by: Mapped[str | None] = mapped_column(String(100))
    fetched_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    filename: Mapped[str | None] = mapped_column(String(255))  # (added)


# --- Review and email ------------------------------------------------------------------------


class ReviewDecision(Base):
    __tablename__ = "review_decision"

    id: Mapped[str] = _id()
    item_id: Mapped[str] = mapped_column(ForeignKey("regulatory_item.id"), index=True)
    reviewer: Mapped[str] = mapped_column(String(100))
    decided_at: Mapped[datetime] = mapped_column(TZDateTime)
    treatment: Mapped[Treatment | None] = mapped_column(
        enum_column(Treatment, "review_decision_treatment")  # empty only on a draft
    )
    include_in_update: Mapped[bool] = mapped_column(Boolean, default=False)
    applicability: Mapped[Applicability | None] = mapped_column(
        enum_column(Applicability, "review_decision_applicability")
    )
    ref_no_and_date: Mapped[str | None] = mapped_column(Text)
    authority: Mapped[str | None] = mapped_column(String(100))
    guideline_title: Mapped[str | None] = mapped_column(Text)
    issue_date: Mapped[date | None] = mapped_column(Date)
    publish_date: Mapped[date | None] = mapped_column(Date)
    effective_date: Mapped[date | None] = mapped_column(Date)
    status_colour: Mapped[StatusColour | None] = mapped_column(
        enum_column(StatusColour, "review_decision_status_colour")
    )
    impact_text: Mapped[str | None] = mapped_column(Text)
    sme_function: Mapped[str | None] = mapped_column(String(100))
    dismiss_reason: Mapped[str | None] = mapped_column(Text)
    fields_edited: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    is_draft: Mapped[bool] = mapped_column(Boolean, default=False)  # (added)


class DailyUpdate(Base):
    __tablename__ = "daily_update"
    __table_args__ = (UniqueConstraint("cycle_date", "cycle"),)

    id: Mapped[str] = _id()
    cycle_date: Mapped[date] = mapped_column(Date)
    cycle: Mapped[Cycle] = mapped_column(enum_column(Cycle, "daily_update_cycle"))
    status: Mapped[UpdateStatus] = mapped_column(
        enum_column(UpdateStatus, "daily_update_status"), default=UpdateStatus.DRAFT
    )
    subject: Mapped[str] = mapped_column(Text)
    html: Mapped[str] = mapped_column(Text, default="")
    sources_ok: Mapped[list[str]] = mapped_column(JSON, default=list)
    sources_failed: Mapped[list[str]] = mapped_column(JSON, default=list)
    approved_by: Mapped[str | None] = mapped_column(String(100))
    approved_at: Mapped[datetime | None] = mapped_column(TZDateTime)
    prepared_by: Mapped[list[str]] = mapped_column(JSON, default=list)  # (added)
    exported_at: Mapped[datetime | None] = mapped_column(TZDateTime)  # (added)


class DailyUpdateItem(Base):
    __tablename__ = "daily_update_item"
    __table_args__ = (UniqueConstraint("update_id", "serial_no"),)

    update_id: Mapped[str] = mapped_column(ForeignKey("daily_update.id"), primary_key=True)
    item_id: Mapped[str] = mapped_column(ForeignKey("regulatory_item.id"), primary_key=True)
    decision_id: Mapped[str] = mapped_column(ForeignKey("review_decision.id"))
    serial_no: Mapped[int] = mapped_column(Integer)


# --- Configuration (versioned) ---------------------------------------------------------------


class SmeFunction(Base):
    __tablename__ = "sme_function"

    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(100))
    head_name: Mapped[str | None] = mapped_column(String(100))
    head_email: Mapped[str | None] = mapped_column(String(200))
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class Recipient(Base):
    __tablename__ = "recipient"

    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200))
    kind: Mapped[RecipientKind] = mapped_column(
        enum_column(RecipientKind, "recipient_kind"), default=RecipientKind.TO
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    version: Mapped[int] = mapped_column(Integer, default=1)


class EmailTemplate(Base):
    __tablename__ = "email_template"

    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(100))
    subject_pattern: Mapped[str] = mapped_column(Text)
    greeting: Mapped[str] = mapped_column(Text)
    intro: Mapped[str] = mapped_column(Text)
    disclaimer: Mapped[str] = mapped_column(Text)
    no_update_text: Mapped[str] = mapped_column(Text)
    sign_off: Mapped[str] = mapped_column(Text)
    sender_name: Mapped[str] = mapped_column(String(100))
    version: Mapped[int] = mapped_column(Integer, default=1)


class Setting(Base):
    __tablename__ = "setting"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    version: Mapped[int] = mapped_column(Integer, default=1)
    description: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[str] = mapped_column(String(100), default="system")
    updated_at: Mapped[datetime] = mapped_column(TZDateTime)
    pending: Mapped[dict[str, Any] | None] = mapped_column(JSON(none_as_null=True))  # (added)


class AppUser(Base):
    __tablename__ = "app_user"

    id: Mapped[str] = _id()
    name: Mapped[str] = mapped_column(String(100))
    email: Mapped[str] = mapped_column(String(200), unique=True)
    role: Mapped[UserRole] = mapped_column(enum_column(UserRole, "app_user_role"))


# --- Audit -----------------------------------------------------------------------------------


class AuditEvent(Base):
    """Append-only: the database rejects UPDATE and DELETE."""

    __tablename__ = "audit_event"
    __table_args__ = (Index("ix_audit_event_entity_entity_id", "entity", "entity_id"),)

    id: Mapped[str] = _id()
    ts: Mapped[datetime] = mapped_column(TZDateTime, index=True)
    actor: Mapped[str] = mapped_column(String(100), index=True)
    action: Mapped[str] = mapped_column(String(100))
    entity: Mapped[str] = mapped_column(String(100))
    entity_id: Mapped[str] = mapped_column(String(100))
    before: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))
    after: Mapped[Any | None] = mapped_column(JSON(none_as_null=True))


APPEND_ONLY_TABLES = ("snapshot", "document", "audit_event")
