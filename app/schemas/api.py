"""Request/response models for /api/v1 and the filters shared with the repository layer."""

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.entities import (
    CrawlRun,
    Document,
    ItemObservation,
    ManualCheck,
    ReviewDecision,
    Setting,
    Snapshot,
    Source,
    SourceVersion,
)
from app.schemas.enums import (
    Applicability,
    ChangeType,
    Channel,
    CheckStatus,
    Cycle,
    ItemState,
    Priority,
    SourceType,
    StatusColour,
    Treatment,
    UpdateStatus,
)

_CYCLE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-([A-Z]+)$")


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CycleRef(Model):
    """A reporting window. Wire format: `2026-11-20-EOD` (ISO date + cycle)."""

    cycle_date: date
    cycle: Cycle

    @classmethod
    def parse(cls, text: str) -> "CycleRef":
        match = _CYCLE_RE.match(text.strip())
        if not match:
            raise ValueError("Cycle must look like 2026-11-20-EOD")
        try:
            return cls(cycle_date=date.fromisoformat(match.group(1)), cycle=Cycle(match.group(2)))
        except ValueError as exc:
            raise ValueError("Cycle must look like 2026-11-20-EOD") from exc

    @property
    def id(self) -> str:
        return f"{self.cycle_date.isoformat()}-{self.cycle.value}"

    @property
    def label(self) -> str:
        names = {
            Cycle.MORNING: "Morning",
            Cycle.EOD: "End of day",
            Cycle.ADHOC: "Ad hoc",
            Cycle.URGENT: "Urgent",
        }
        return f"{self.cycle_date.strftime('%d.%m.%Y')} · {names[self.cycle]}"


class ItemFilter(Model):
    cycle: CycleRef | None = None
    state: ItemState | None = None
    channel: Channel | None = None
    q: str | None = None
    min_relevance: int | None = None


# --- Runs and source health ------------------------------------------------------------------


class SourceCheckView(Model):
    id: str
    run_id: str
    source_id: str
    authority: str
    page_name: str
    channel: str
    status: CheckStatus
    status_label: str
    http_status: int | None = None
    items_found: int | None = None
    error_detail: str | None = None
    snapshot_id: str | None = None
    needs_acknowledgement: bool
    manual_check: ManualCheck | None = None


class RunDetail(Model):
    run: CrawlRun
    checks: list[SourceCheckView]
    sources_total: int
    sources_done: int


class SourceHealthSummary(Model):
    cycle: str
    cycle_label: str
    run_id: str | None
    last_run_started_at: datetime | None = None
    last_run_ended_at: datetime | None = None
    sources_total: int
    sources_ok: int
    sources_manual: int
    headline: str
    checks: list[SourceCheckView]
    attention: list[SourceCheckView]


class ManualCheckIn(Model):
    note: str = Field(min_length=1, max_length=1000)

    @field_validator("note")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("A note is required")
        return v.strip()


class CrawlRunIn(Model):
    source_id: str | None = None


# --- Items -----------------------------------------------------------------------------------

ItemSection = Literal["attention", "manual_fetch", "low_relevance"]


class LunaChip(Model):
    """Luna's reading of an item, shown next to our own rank. A suggestion, never a decision."""

    level: Literal["HIGH", "MEDIUM", "LOW", "NONE"] | None = None
    score: float | None = None
    disagreement: bool = False  # our rules and Luna are two levels or more apart


class LunaSuggestions(Model):
    """What Luna suggested for one item, with the quotes that were checked against the text."""

    level: Literal["HIGH", "MEDIUM", "LOW", "NONE"] | None = None
    score: float | None = None
    reason: str | None = None
    addressed_to: list[str] = Field(default_factory=list)
    treatment: str | None = None
    sme_function: str | None = None
    urgency: str | None = None
    evidence: list[dict[str, Any]] = Field(default_factory=list)  # validated quotes only
    fields: dict[str, dict[str, Any]] = Field(default_factory=dict)  # extract: value + quote
    model: str | None = None
    deployment: str | None = None
    prompt_version: str | None = None
    guidance_version: int | None = None
    created_at: datetime | None = None
    disagreement: dict[str, Any] | None = None  # {rule_level, luna_level, ...} when flagged


class AgentStatusOut(Model):
    enabled: bool  # environment flag AND the setting
    env_enabled: bool
    setting_enabled: bool
    configured: bool
    problem: str | None = None
    daily_budget_inr: float
    spent_today_inr: float
    open_flags: int


class AgentFlagView(Model):
    id: str
    type: Literal["POSSIBLE_MISS", "DISAGREEMENT"]
    status: Literal["OPEN", "RESOLVED", "FALSE_ALARM"]
    source_id: str | None
    authority: str | None
    page_name: str | None
    item_id: str | None
    item_title: str | None
    snapshot_id: str | None
    payload: dict[str, Any]
    created_at: datetime
    resolved_by: str | None = None
    resolved_at: datetime | None = None


class ResolveFlagIn(Model):
    status: Literal["RESOLVED", "FALSE_ALARM"]


class ItemSummary(Model):
    id: str
    internal_ref: str
    source_id: str
    authority: str
    page_name: str
    title: str
    canonical_url: str
    ref_no: str | None
    displayed_date: date | None
    channel: Channel
    channel_label: str
    channel_mismatch: bool
    relevance_score: int
    relevance_label: Literal["High", "Medium", "Low"]
    relevance_reason: str | None
    priority: Priority
    state: ItemState
    state_label: str
    change_type: ChangeType | None
    badges: list[str]
    note: str | None
    summary: str | None
    section: ItemSection
    decided: bool
    first_seen_at: datetime
    luna: LunaChip | None = None


class ItemCounts(Model):
    new: int
    modified: int
    backdated: int
    reappeared: int
    urgent: int
    needs_manual_fetch: int
    decided: int
    total: int
    low_relevance: int


class ItemListResponse(Model):
    cycle: str | None
    counts: ItemCounts
    items: list[ItemSummary]


class ApplicabilitySuggestion(Model):
    """A suggestion from the Entity Profile (FR-35). The reviewer decides."""

    suggested: Applicability
    text: str
    phrase: str
    profile_item: str


class Prefill(Model):
    """Values the system proposes for the review form. Used to track fields the reviewer edits."""

    ref_no_and_date: str | None
    authority: str | None
    guideline_title: str | None
    issue_date: date | None
    publish_date: date | None
    effective_date: date | None
    effective_date_rule: str | None
    applicability_hint: ApplicabilitySuggestion | None = None


class TraceStep(Model):
    kind: Literal["email", "decision", "document", "detection", "snapshot", "source_version", "run"]
    title: str
    detail: str
    sha256: str | None = None
    at: datetime | None = None


class ItemDetail(Model):
    item: ItemSummary
    prefill: Prefill
    latest_decision: ReviewDecision | None
    decisions: list[ReviewDecision]
    observations: list[ItemObservation]
    snapshots: list[Snapshot]
    documents: list[Document]
    source: Source
    source_version: SourceVersion | None
    chain: list[TraceStep]
    luna: LunaSuggestions | None = None
    luna_enabled: bool = False


class DecisionIn(Model):
    action: Literal["accept", "save", "dismiss"]
    treatment: Treatment | None = None
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
    sme_function: str | None = None
    dismiss_reason: str | None = None


class DecisionOut(Model):
    decision: ReviewDecision
    item: ItemSummary


class BulkDismissIn(Model):
    item_ids: list[str] = Field(min_length=1)
    reason: str = Field(min_length=1, max_length=500)


class BulkDismissOut(Model):
    dismissed: list[str]


# --- Daily update ----------------------------------------------------------------------------


class DailyUpdateRow(Model):
    serial_no: int
    item_id: str
    decision_id: str
    ref_no_and_date: str
    date_note: str | None = None  # FR-33: shown under the reference when issue/publish dates differ
    authority: str
    guideline_title: str
    link: str
    applicability: str
    effective_date: str
    status_colour: StatusColour | None
    status_label: str | None
    status_hex: str | None
    impact_text: str


class PreCheck(Model):
    ok: bool
    text: str


class DailyUpdatePreview(Model):
    id: str
    cycle: str
    status: UpdateStatus
    subject: str
    to: list[str]
    cc: list[str]
    rows: list[DailyUpdateRow]
    no_update: bool
    html: str
    checks: list[PreCheck]
    can_approve: bool
    approved_by: str | None
    approved_at: datetime | None


class SmeFunctionOption(Model):
    id: str
    name: str


class ReorderIn(Model):
    item_ids: list[str] = Field(min_length=1)


# --- Sources and settings --------------------------------------------------------------------


class FieldChange(Model):
    """One changed field in plain language (before and after as readable text)."""

    label: str
    before: str
    after: str


class KeyValue(Model):
    key: str
    value: str


class SourceView(Model):
    source: Source
    how_we_read_it: str
    status_label: str
    pending_version: SourceVersion | None
    pending_changes: list[FieldChange] = Field(default_factory=list)
    can_edit: bool = False
    can_decide: bool = False  # the current user may approve or reject the pending version
    decide_note: str | None = None  # why not, in plain words


class SourceChanges(Model):
    """Partial source fields. Only fields that are sent are changed."""

    authority: str | None = None
    page_name: str | None = None
    type: SourceType | None = None
    url: str | None = None
    rss_url: str | None = None
    adapter_config: dict[str, Any] | None = None
    include_keywords: list[str] | None = None
    exclude_keywords: list[str] | None = None
    priority: Priority | None = None
    requires_js: bool | None = None
    active: bool | None = None


class SourceCreate(Model):
    # Made from the authority and page name when left empty.
    id: str | None = Field(None, pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
    authority: str = Field(min_length=1)
    page_name: str = Field(min_length=1)
    type: SourceType
    url: str = Field(min_length=1)
    rss_url: str | None = None
    adapter_config: dict[str, Any] = Field(default_factory=dict)
    include_keywords: list[str] = Field(default_factory=list)
    exclude_keywords: list[str] = Field(default_factory=list)
    priority: Priority = Priority.NORMAL
    requires_js: bool = False


class VersionRejectIn(Model):
    reason: str = Field(min_length=1, max_length=500)


class SourceTestRow(Model):
    title: str
    link: str
    displayed_date: date | None = None
    ref_no: str | None = None


class SourceTestResult(Model):
    source_id: str
    status: CheckStatus
    duration_seconds: float
    rows: list[SourceTestRow]
    message: str
    saved: bool = False


class SettingIn(Model):
    value: Any


class SettingRejectIn(Model):
    reason: str = Field(min_length=1, max_length=500)


class SettingView(Model):
    setting: Setting
    has_pending: bool
    title: str = ""
    changes: list[FieldChange] = Field(default_factory=list)  # pending value vs live value
    can_edit: bool = False
    can_decide: bool = False
    decide_note: str | None = None


# --- Audit log -------------------------------------------------------------------------------


class AuditRow(Model):
    id: str
    ts: datetime
    actor: str
    action: str
    action_label: str
    entity: str
    entity_id: str
    before: list[KeyValue]
    after: list[KeyValue]


class AuditOptions(Model):
    actors: list[str]
    entities: list[str]
    actions: list[str]


class AuditPage(Model):
    total: int
    rows: list[AuditRow]
    options: AuditOptions


# --- Source health grid ----------------------------------------------------------------------

CellTone = Literal["ok", "grey", "mod", "urg"]


class GridAction(Model):
    """A link or button in the Action column. `kind` tells the page which call to make."""

    kind: Literal["snapshot", "compare", "log", "manual"]
    label: str
    snapshot_id: str | None = None
    check_id: str | None = None


class GridCell(Model):
    cycle: str
    check_id: str | None
    label: str  # e.g. "OK", "Zero items", "1 mismatch", "Blocked", "Failed (timeout)"
    tone: CellTone
    status: CheckStatus | None


class GridRow(Model):
    source_id: str
    authority: str
    page_name: str
    channel: str
    cells: list[GridCell]
    items_latest: str  # "10", "1 change" or "—"
    possible_misses: int = 0  # open "possible missed item" flags from Luna's sweep
    needs_attention: bool
    actions: list[GridAction]


class SourceGrid(Model):
    cycles: list[CycleRef]
    cycle_labels: list[str]
    rows: list[GridRow]
    current_run: CrawlRun | None


class CompareItem(Model):
    title: str
    ref_no: str | None
    displayed_date: date | None
    seen_in: Literal["Both", "RSS only", "HTML only"]


class CompareResult(Model):
    source_id: str
    cycle: str
    authority: str
    page_name: str
    both: int
    rss_only: int
    html_only: int
    items: list[CompareItem]
    message: str


class CheckLog(Model):
    check: SourceCheckView
    cycle_label: str
    run_started_at: datetime | None
    run_ended_at: datetime | None
    lines: list[str]


# --- Item history ----------------------------------------------------------------------------


class HistoryHit(Model):
    id: str
    title: str
    authority: str
    internal_ref: str
    note: str  # "Modified 20.11.2026" or "First seen 03.11.2026"


class HistoryResults(Model):
    total: int
    hits: list[HistoryHit]
    authorities: list[str]


class TrailHash(Model):
    label: str
    sha256: str
    short: str


class TrailLink(Model):
    label: str
    href: str


class TrailBadge(Model):
    tone: Literal["ok", "grey", "mod", "new", "urg"]
    label: str


class TrailStep(Model):
    number: int
    kind: Literal["email", "decision", "agent", "document", "change", "snapshot", "source_config"]
    title: str
    badge: TrailBadge | None = None
    text: str
    note: str | None = None
    hashes: list[TrailHash] = Field(default_factory=list)
    links: list[TrailLink] = Field(default_factory=list)
    present: bool = True  # False when this stage has not happened yet for the item
    at: datetime | None = None
    who: str | None = None


class Trail(Model):
    item_id: str
    internal_ref: str
    title: str
    steps: list[TrailStep]
