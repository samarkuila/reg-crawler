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
    change_type: ChangeType | None
    badges: list[str]
    note: str | None
    summary: str | None
    section: ItemSection
    decided: bool
    first_seen_at: datetime


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


class Prefill(Model):
    """Values the system proposes for the review form. Used to track fields the reviewer edits."""

    ref_no_and_date: str | None
    authority: str | None
    guideline_title: str | None
    issue_date: date | None
    publish_date: date | None
    effective_date: date | None
    effective_date_rule: str | None


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


class ReorderIn(Model):
    item_ids: list[str] = Field(min_length=1)


# --- Sources and settings --------------------------------------------------------------------


class SourceView(Model):
    source: Source
    how_we_read_it: str
    status_label: str
    pending_version: SourceVersion | None


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
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]{1,60}$")
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
