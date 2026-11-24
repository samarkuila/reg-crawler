"""Pydantic v2 models for every entity in architecture.md §4.

Fields follow the architecture tables. Additions are marked "(added)" and are listed in
docs/architecture.md §4 notes. `snapshot`, `document` and `audit_event` are append-only:
repositories expose no update/delete for them.
"""

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

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

IST = timezone(timedelta(hours=5, minutes=30))


def now_ist() -> datetime:
    return datetime.now(UTC).astimezone(IST)


class Entity(BaseModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True)


# --- Sources ---------------------------------------------------------------------------------


class Source(Entity):
    id: str
    authority: str
    page_name: str
    type: SourceType
    url: str
    rss_url: str | None = None
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    priority: Priority = Priority.NORMAL
    requires_js: bool = False
    active: bool = False
    current_version: int = 0
    planned: bool = False  # (added) on the monitoring list but not in the POC; always inactive
    retired: bool = False  # (added) deleted from the register; kept so history stays whole
    # First-crawl window (added 24.11.2026). None = use crawler.first_run in settings.yaml.
    first_run_mode: FirstRunMode | None = None
    first_run_days: int | None = Field(None, ge=1, le=3650)
    sorted_newest_first: bool = True  # the page lists newest first, so paging may stop early
    baseline_status: BaselineStatus = BaselineStatus.NOT_BASELINED
    baseline_at: datetime | None = None
    baseline_window_start: date | None = None

    @property
    def needs_browser(self) -> bool:
        """Whether a crawl must render the page in a browser.

        An RSS feed or HTML listing never does, whatever an older version stored in requires_js.
        """
        return self.requires_js and self.type not in (SourceType.RSS, SourceType.HTML_LIST)


class SourceVersion(Entity):
    id: str
    source_id: str
    version: int
    before: dict[str, Any] = Field(default_factory=dict)
    after: dict[str, Any] = Field(default_factory=dict)
    status: VersionStatus = VersionStatus.PENDING
    requested_by: str
    approved_by: str | None = None
    ts: datetime
    decided_at: datetime | None = None  # (added) when approved/rejected
    reject_reason: str | None = None  # (added)


# --- Runs and evidence -----------------------------------------------------------------------


class CrawlRun(Entity):
    id: str
    cycle_date: date
    cycle: Cycle
    triggered_by: str
    started_at: datetime
    ended_at: datetime | None = None
    status: RunStatus = RunStatus.RUNNING
    sources: list[str] | None = None  # (added, Prompt 13) source ids this run covers; None = all


class SourceCheck(Entity):
    id: str
    run_id: str
    source_id: str
    channel: CheckChannel
    status: CheckStatus
    http_status: int | None = None
    items_found: int | None = None
    error_detail: str | None = None
    snapshot_id: str | None = None
    # Run counters (added): rows_parsed, pages_read, requests_made, window_start from the crawler;
    # new, modified, backdated, reappeared, unchanged, known_older, baseline_items from detection.
    stats: dict[str, Any] = Field(default_factory=dict)


class Snapshot(Entity):
    """Append-only."""

    id: str
    source_id: str
    channel: CheckChannel
    url: str
    fetched_at: datetime
    sha256: str
    path: str


class Alert(Entity):
    """(added, Prompt 13) Something a person should look at after a run.

    Shown as a banner now; `delivered_at` is for the V2 Teams/email notifier, which sends the
    alerts it has not delivered yet. Alerts are never deleted.
    """

    id: str
    run_id: str | None = None
    kind: AlertKind
    severity: Literal["urgent", "warning"] = "warning"
    source_id: str | None = None
    item_id: str | None = None
    message: str
    created_at: datetime
    delivered_at: datetime | None = None


class AiExtraction(Entity):
    """Append-only. What Luna suggested for an item. Never a decision (policy A1, A9)."""

    id: str
    item_id: str
    task: AgentTask  # SCREEN | EXTRACT
    # Each field: {value, evidence_quote, location, confidence, validated}
    fields: dict[str, Any] = Field(default_factory=dict)
    nbfc_relevance: NbfcRelevance | None = None
    relevance_score: float | None = None
    suggested_treatment: str | None = None
    suggested_sme_function: str | None = None
    model: str
    deployment: str | None = None
    prompt_version: str
    guidance_version: int | None = None
    created_at: datetime


class AgentFlag(Entity):
    """A possible missed item (from the sweep) or a rules-vs-Luna disagreement, for a person."""

    id: str
    run_id: str | None = None
    source_id: str | None = None
    snapshot_id: str | None = None
    item_id: str | None = None
    type: FlagType
    payload: dict[str, Any] = Field(default_factory=dict)
    status: FlagStatus = FlagStatus.OPEN
    resolved_by: str | None = None
    resolved_at: datetime | None = None
    created_at: datetime


class AgentRun(Entity):
    """One LLM call, logged whatever happened (spec §5.9)."""

    id: str
    run_id: str | None = None
    task: AgentTask
    item_id: str | None = None
    snapshot_id: str | None = None
    model: str
    deployment: str | None = None
    prompt_version: str
    guidance_version: int | None = None
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_inr: float = 0.0
    status: AgentStatus
    detail: str | None = None
    created_at: datetime


class RawListingItem(Entity):
    """(added, Prompt 9) One row exactly as a spider read it from a listing, feed or list.

    Detection (Prompt 12) turns these into fingerprints, items and observations. The crawler
    never decides what is new, so nothing here is a regulatory item yet.
    """

    id: str
    run_id: str
    source_id: str
    channel: CheckChannel
    snapshot_id: str | None = None
    title: str
    url: str  # canonical URL
    ref_no: str | None = None
    displayed_date: date | None = None  # as shown on the site; never used to decide "new"
    raw_extra: dict[str, Any] = Field(default_factory=dict)
    fetched_at: datetime


class RegulatoryItem(Entity):
    id: str
    internal_ref: str  # TVSC-REG-YYYY-NNNNNN
    source_id: str
    fingerprint: str
    canonical_url: str
    title: str
    ref_no: str | None = None
    displayed_date: date | None = None
    channel: Channel
    channel_mismatch: bool = False
    relevance_score: int = 0  # 0–100, keyword rules (ranking only, never hides)
    relevance_reason: str | None = None
    priority: Priority = Priority.NORMAL
    state: ItemState = ItemState.DETECTED
    first_seen_run_id: str
    first_seen_at: datetime
    last_seen_at: datetime
    summary: str | None = None  # (added) one-line change summary, e.g. UNSC "alias added"
    origin: ItemOrigin = ItemOrigin.CRAWL  # (added) BASELINE = stored by the first crawl window
    flags: list[str] = Field(default_factory=list)  # (added) e.g. "old date, new on page"


class SeenFingerprint(Entity):
    """Insert-only. An item a source's first crawl saw but did not store (older than the window).

    Only its fingerprint is kept, so later crawls can tell a known-old item from a new one.
    """

    id: str
    source_id: str
    fingerprint: str
    title: str
    published_date: date | None = None
    first_seen_run_id: str
    first_seen_at: datetime
    reason: SeenReason


class ItemObservation(Entity):
    id: str
    item_id: str
    run_id: str
    channel: CheckChannel
    change_type: ChangeType
    prior_hash: str | None = None
    current_hash: str
    snapshot_id: str | None = None


class Document(Entity):
    """Append-only."""

    id: str
    item_id: str
    url: str | None = None
    sha256: str | None = None
    path: str | None = None
    mime: str | None = None
    text_path: str | None = None
    fetch_status: FetchStatus
    uploaded_by: str | None = None
    fetched_at: datetime | None = None
    filename: str | None = None  # (added) original name of a manual upload
    needs_ocr: bool = False  # (added, Prompt 12) a scanned PDF: no usable text layer


# --- Review and email ------------------------------------------------------------------------


class ReviewDecision(Entity):
    id: str
    item_id: str
    reviewer: str
    decided_at: datetime
    treatment: Treatment | None = None  # empty only for a saved draft
    include_in_update: bool = False
    applicability: Applicability | None = None
    ref_no_and_date: str | None = None
    authority: str | None = None
    guideline_title: str | None = None
    issue_date: date | None = None
    publish_date: date | None = None
    effective_date: date | None = None
    status_colour: StatusColour | None = None
    impact_text: str | None = None
    sme_function: str | None = None  # SmeFunction.id
    dismiss_reason: str | None = None
    fields_edited: dict[str, Any] = Field(default_factory=dict)
    is_draft: bool = False  # (added) "Save" keeps a draft; only Accept/Dismiss are final


class DailyUpdate(Entity):
    id: str
    cycle_date: date
    cycle: Cycle
    status: UpdateStatus = UpdateStatus.DRAFT
    subject: str
    html: str = ""
    sources_ok: list[str] = Field(default_factory=list)
    sources_failed: list[str] = Field(default_factory=list)
    approved_by: str | None = None
    approved_at: datetime | None = None
    prepared_by: list[str] = Field(default_factory=list)  # (added) people who prepared the rows
    exported_at: datetime | None = None  # (added)


class DailyUpdateItem(Entity):
    update_id: str
    item_id: str
    decision_id: str
    serial_no: int


class ManualCheck(Entity):
    id: str
    source_check_id: str
    user: str
    note: str
    ts: datetime


# --- Configuration ---------------------------------------------------------------------------


class SmeFunction(Entity):
    id: str
    name: str
    head_name: str | None = None
    head_email: str | None = None
    active: bool = True
    version: int = 1


class Recipient(Entity):
    id: str
    name: str
    email: str
    kind: RecipientKind = RecipientKind.TO
    active: bool = True
    version: int = 1


class EmailTemplate(Entity):
    id: str
    name: str
    subject_pattern: str
    greeting: str
    intro: str
    disclaimer: str
    no_update_text: str
    sign_off: str
    sender_name: str
    version: int = 1


class SettingChange(Entity):
    """A proposed new value waiting for approval by a different user (maker–checker)."""

    value: Any
    requested_by: str
    requested_at: datetime
    version: int


class Setting(Entity):
    key: str
    value: Any
    version: int = 1
    description: str | None = None
    updated_by: str = "system"
    updated_at: datetime
    pending: SettingChange | None = None  # (added)


class AppUser(Entity):
    id: str
    name: str
    email: str
    role: UserRole


class AuditEvent(Entity):
    """Append-only."""

    id: str
    ts: datetime
    actor: str
    action: str
    entity: str
    entity_id: str
    before: Any = None
    after: Any = None
