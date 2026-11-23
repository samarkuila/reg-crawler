"""In-memory Repository for the mock-data milestone (REPO_BACKEND=memory).

Behaves like the future SQL repository: every mutation runs in a "transaction" (rolled back if
anything fails) and writes its AuditEvent inside it. Objects are copied on the way in and out so
callers cannot change stored state, and snapshots/documents/audit events have no update or delete.
"""

import copy
import hashlib
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date, datetime
from typing import Any

from pydantic import BaseModel

from app.schemas.api import CycleRef, ItemFilter
from app.schemas.entities import (
    AgentFlag,
    AgentRun,
    AiExtraction,
    Alert,
    AppUser,
    AuditEvent,
    CrawlRun,
    DailyUpdate,
    DailyUpdateItem,
    Document,
    EmailTemplate,
    ItemObservation,
    ManualCheck,
    RawListingItem,
    Recipient,
    RegulatoryItem,
    ReviewDecision,
    Setting,
    SettingChange,
    SmeFunction,
    Snapshot,
    Source,
    SourceCheck,
    SourceVersion,
    now_ist,
)
from app.schemas.enums import Cycle, FlagStatus, ItemState, RunStatus, UpdateStatus, VersionStatus

_COLLECTIONS: dict[type[BaseModel], str] = {
    AppUser: "users",
    Source: "sources",
    SourceVersion: "source_versions",
    CrawlRun: "runs",
    SourceCheck: "checks",
    Snapshot: "snapshots",
    RawListingItem: "raw_items",
    Alert: "alerts",
    AiExtraction: "extractions",
    AgentRun: "agent_runs",
    AgentFlag: "agent_flags",
    RegulatoryItem: "items",
    ItemObservation: "observations",
    Document: "documents",
    ReviewDecision: "decisions",
    DailyUpdate: "daily_updates",
    ManualCheck: "manual_checks",
    SmeFunction: "sme_functions",
    Recipient: "recipients",
    EmailTemplate: "email_templates",
    AuditEvent: "audit",
}


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _dump(obj: BaseModel | None) -> Any:
    return None if obj is None else obj.model_dump(mode="json")


class MemoryRepository:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._s: dict[str, dict[str, Any]] = {name: {} for name in _COLLECTIONS.values()}
        self._settings: dict[str, Setting] = {}
        self._update_rows: dict[str, list[DailyUpdateItem]] = {}
        self._blobs: dict[str, bytes] = {}
        self._texts: dict[str, str] = {}

    # --- internals ---------------------------------------------------------------------------

    @contextmanager
    def _txn(self) -> Iterator[None]:
        """All-or-nothing: restore the previous state if the block raises."""
        with self._lock:
            backup = copy.deepcopy(
                (self._s, self._settings, self._update_rows, self._blobs, self._texts)
            )
            try:
                yield
            except BaseException:
                self._s, self._settings, self._update_rows, self._blobs, self._texts = backup
                raise

    def _audit(
        self, actor: str, action: str, entity: str, entity_id: str, before: Any, after: Any
    ) -> None:
        event = AuditEvent(
            id=new_id("aud"),
            ts=now_ist(),
            actor=actor,
            action=action,
            entity=entity,
            entity_id=entity_id,
            before=before,
            after=after,
        )
        self._s["audit"][event.id] = event

    def _get(self, collection: str, key: str) -> Any | None:
        obj = self._s[collection].get(key)
        return None if obj is None else obj.model_copy(deep=True)

    def _all(self, collection: str) -> list[Any]:
        return [o.model_copy(deep=True) for o in self._s[collection].values()]

    def load_blob(self, sha256: str, content: bytes) -> None:
        """Seed loader for stored copies (content-addressed by SHA-256)."""
        with self._lock:
            self._blobs[sha256] = content

    def read_blob(self, sha256: str) -> bytes | None:
        """Stored copy of a snapshot or document, or None when the file is not held."""
        with self._lock:
            return self._blobs.get(sha256)

    def load(self, entities: Iterable[BaseModel]) -> None:
        """Seed loader: puts entities in without audit events (seed data is not a user action)."""
        with self._lock:
            for obj in entities:
                if isinstance(obj, Setting):
                    self._settings[obj.key] = obj.model_copy(deep=True)
                elif isinstance(obj, DailyUpdateItem):
                    self._update_rows.setdefault(obj.update_id, []).append(obj.model_copy())
                else:
                    self._s[_COLLECTIONS[type(obj)]][obj.id] = obj.model_copy(deep=True)  # type: ignore[attr-defined]

    # --- users -------------------------------------------------------------------------------

    def list_users(self) -> list[AppUser]:
        # Same order on every backend: reviewer, approver, admin, viewer; then by name.
        rank = {"reviewer": 0, "approver": 1, "admin": 2, "viewer": 3}
        return sorted(self._all("users"), key=lambda u: (rank[u.role.value], u.name))

    def get_user(self, user_id: str) -> AppUser | None:
        return self._get("users", user_id)

    # --- sources and versions ----------------------------------------------------------------

    def list_sources(self) -> list[Source]:
        return self._all("sources")

    def get_source(self, source_id: str) -> Source | None:
        return self._get("sources", source_id)

    def create_source(self, source: Source, version: SourceVersion, actor: str) -> Source:
        with self._txn():
            if source.id in self._s["sources"]:
                raise ValueError(f"Source {source.id} already exists")
            self._s["sources"][source.id] = source.model_copy(deep=True)
            self._s["source_versions"][version.id] = version.model_copy(deep=True)
            self._audit(actor, "source.created", "source", source.id, None, _dump(source))
            self._audit(
                actor,
                "source_version.requested",
                "source_version",
                version.id,
                None,
                _dump(version),
            )
            return source.model_copy(deep=True)

    def list_source_versions(self, source_id: str) -> list[SourceVersion]:
        versions = [v for v in self._all("source_versions") if v.source_id == source_id]
        return sorted(versions, key=lambda v: v.version)

    def get_source_version(self, source_id: str, version: int) -> SourceVersion | None:
        for v in self.list_source_versions(source_id):
            if v.version == version:
                return v
        return None

    def add_source_version(
        self, source_id: str, after: dict[str, Any], actor: str
    ) -> SourceVersion:
        with self._txn():
            source = self._s["sources"][source_id]
            current = source.model_dump(mode="json")
            versions = self.list_source_versions(source_id)
            ver = SourceVersion(
                id=new_id("srcv"),
                source_id=source_id,
                version=max((v.version for v in versions), default=0) + 1,
                before={k: current.get(k) for k in after},
                after=after,
                status=VersionStatus.PENDING,
                requested_by=actor,
                ts=now_ist(),
            )
            self._s["source_versions"][ver.id] = ver
            self._audit(
                actor, "source_version.requested", "source_version", ver.id, None, _dump(ver)
            )
            return ver.model_copy(deep=True)

    def _find_version(self, source_id: str, version: int) -> SourceVersion:
        for v in self._s["source_versions"].values():
            if v.source_id == source_id and v.version == version:
                return v
        raise KeyError(f"{source_id} v{version}")

    def approve_source_version(
        self, source_id: str, version: int, approver: str, actor: str
    ) -> SourceVersion:
        with self._txn():
            ver = self._find_version(source_id, version)
            if ver.status != VersionStatus.PENDING:
                raise ValueError("Only a pending version can be approved")
            source = self._s["sources"][source_id]
            before_source = _dump(source)
            changes = dict(ver.after)
            if changes.get("active"):
                changes["planned"] = False
            merged = {**source.model_dump(), **changes, "current_version": version}
            self._s["sources"][source_id] = Source.model_validate(merged)
            before_ver = _dump(ver)
            ver.status = VersionStatus.APPROVED
            ver.approved_by = approver
            ver.decided_at = now_ist()
            self._audit(
                actor, "source_version.approved", "source_version", ver.id, before_ver, _dump(ver)
            )
            self._audit(
                actor,
                "source.updated",
                "source",
                source_id,
                before_source,
                _dump(self._s["sources"][source_id]),
            )
            return ver.model_copy(deep=True)

    def reject_source_version(
        self, source_id: str, version: int, approver: str, reason: str, actor: str
    ) -> SourceVersion:
        with self._txn():
            ver = self._find_version(source_id, version)
            if ver.status != VersionStatus.PENDING:
                raise ValueError("Only a pending version can be rejected")
            before_ver = _dump(ver)
            ver.status = VersionStatus.REJECTED
            ver.approved_by = approver
            ver.decided_at = now_ist()
            ver.reject_reason = reason
            self._audit(
                actor, "source_version.rejected", "source_version", ver.id, before_ver, _dump(ver)
            )
            return ver.model_copy(deep=True)

    # --- runs, checks, snapshots -------------------------------------------------------------

    def list_runs(self, cycle_date: date | None = None) -> list[CrawlRun]:
        runs = self._all("runs")
        if cycle_date is not None:
            runs = [r for r in runs if r.cycle_date == cycle_date]
        return sorted(runs, key=lambda r: r.started_at, reverse=True)

    def list_cycles(self) -> list[CycleRef]:
        seen: list[CycleRef] = []
        for run in self.list_runs():
            ref = CycleRef(cycle_date=run.cycle_date, cycle=run.cycle)
            if ref not in seen:
                seen.append(ref)
        return seen

    def get_run(self, run_id: str) -> CrawlRun | None:
        return self._get("runs", run_id)

    def find_runs(self, ref: CycleRef) -> list[CrawlRun]:
        return [r for r in self.list_runs(ref.cycle_date) if r.cycle == ref.cycle]

    def create_run(
        self,
        cycle: Cycle,
        cycle_date: date,
        triggered_by: str,
        actor: str,
        sources: list[str] | None = None,
    ) -> CrawlRun:
        with self._txn():
            run = CrawlRun(
                id=new_id("run"),
                cycle_date=cycle_date,
                cycle=cycle,
                triggered_by=triggered_by,
                started_at=now_ist(),
                status=RunStatus.RUNNING,
                sources=list(sources) if sources is not None else None,
            )
            self._s["runs"][run.id] = run
            self._audit(actor, "crawl_run.created", "crawl_run", run.id, None, _dump(run))
            return run.model_copy(deep=True)

    # --- Luna agent ----------------------------------------------------------------------------

    def save_extraction(self, ext: AiExtraction, enrich: bool, actor: str) -> AiExtraction:
        with self._txn():
            if ext.item_id not in self._s["items"]:
                raise KeyError(ext.item_id)
            self._s["extractions"][ext.id] = ext.model_copy(deep=True)
            self._audit(actor, "ai_extraction.saved", "ai_extraction", ext.id, None, _dump(ext))
            if enrich and self._s["items"][ext.item_id].state == ItemState.DETECTED:
                self._set_state(ext.item_id, ItemState.ENRICHED, actor)
            return ext.model_copy(deep=True)

    def list_extractions(self, item_id: str) -> list[AiExtraction]:
        found = [e for e in self._all("extractions") if e.item_id == item_id]
        return sorted(found, key=lambda e: (e.created_at, e.id))

    def add_agent_run(self, run: AgentRun, actor: str) -> AgentRun:
        with self._txn():
            self._s["agent_runs"][run.id] = run.model_copy(deep=True)
            self._audit(actor, "agent_run.logged", "agent_run", run.id, None, _dump(run))
            return run.model_copy(deep=True)

    def list_agent_runs(self, day: date | None = None) -> list[AgentRun]:
        found = [r for r in self._all("agent_runs") if day is None or r.created_at.date() == day]
        return sorted(found, key=lambda r: (r.created_at, r.id))

    def add_agent_flags(self, flags: list[AgentFlag], actor: str) -> list[AgentFlag]:
        if not flags:
            return []
        with self._txn():
            for flag in flags:
                self._s["agent_flags"][flag.id] = flag.model_copy(deep=True)
            self._audit(
                actor,
                "agent_flags.recorded",
                "agent_flag",
                flags[0].id,
                None,
                {"count": len(flags), "types": sorted({f.type.value for f in flags})},
            )
            return [f.model_copy(deep=True) for f in flags]

    def list_agent_flags(
        self,
        source_id: str | None = None,
        run_id: str | None = None,
        item_id: str | None = None,
        status: FlagStatus | None = None,
    ) -> list[AgentFlag]:
        found = [
            f
            for f in self._all("agent_flags")
            if (source_id is None or f.source_id == source_id)
            and (run_id is None or f.run_id == run_id)
            and (item_id is None or f.item_id == item_id)
            and (status is None or f.status == status)
        ]
        return sorted(found, key=lambda f: (f.created_at, f.id))

    def resolve_agent_flag(
        self, flag_id: str, status: FlagStatus, resolved_by: str, actor: str
    ) -> AgentFlag:
        with self._txn():
            flag = self._s["agent_flags"][flag_id]
            before = _dump(flag)
            flag.status, flag.resolved_by, flag.resolved_at = status, resolved_by, now_ist()
            self._audit(actor, "agent_flag.resolved", "agent_flag", flag_id, before, _dump(flag))
            return flag.model_copy(deep=True)

    def close_run(self, run_id: str, status: RunStatus, actor: str) -> CrawlRun:
        with self._txn():
            run = self._s["runs"][run_id]
            before = _dump(run)
            run.status = status
            run.ended_at = now_ist()
            self._audit(actor, "crawl_run.closed", "crawl_run", run_id, before, _dump(run))
            return run.model_copy(deep=True)

    def add_alerts(self, alerts: list[Alert], actor: str) -> list[Alert]:
        if not alerts:
            return []
        with self._txn():
            for alert in alerts:
                self._s["alerts"][alert.id] = alert.model_copy(deep=True)
            self._audit(
                actor,
                "alerts.recorded",
                "crawl_run",
                alerts[0].run_id or alerts[0].id,
                None,
                {"count": len(alerts), "kinds": sorted({a.kind.value for a in alerts})},
            )
            return [a.model_copy(deep=True) for a in alerts]

    def list_alerts(self, run_id: str | None = None) -> list[Alert]:
        found = [a for a in self._all("alerts") if run_id is None or a.run_id == run_id]
        return sorted(found, key=lambda a: (a.created_at, a.id))

    def list_source_checks(self, run_id: str | None = None) -> list[SourceCheck]:
        checks = self._all("checks")
        if run_id is not None:
            checks = [c for c in checks if c.run_id == run_id]
        return sorted(checks, key=lambda c: c.id)

    def get_source_check(self, check_id: str) -> SourceCheck | None:
        return self._get("checks", check_id)

    def record_source_check(self, check: SourceCheck, actor: str) -> SourceCheck:
        with self._txn():
            self._s["checks"][check.id] = check.model_copy(deep=True)
            self._audit(
                actor, "source_check.recorded", "source_check", check.id, None, _dump(check)
            )
            return check.model_copy(deep=True)

    def get_manual_check(self, source_check_id: str) -> ManualCheck | None:
        for m in self._s["manual_checks"].values():
            if m.source_check_id == source_check_id:
                return m.model_copy(deep=True)
        return None

    def add_manual_check(
        self, source_check_id: str, user: str, note: str, actor: str
    ) -> ManualCheck:
        with self._txn():
            if source_check_id not in self._s["checks"]:
                raise KeyError(source_check_id)
            check = ManualCheck(
                id=new_id("mchk"),
                source_check_id=source_check_id,
                user=user,
                note=note,
                ts=now_ist(),
            )
            self._s["manual_checks"][check.id] = check
            self._audit(actor, "manual_check.added", "manual_check", check.id, None, _dump(check))
            return check.model_copy(deep=True)

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        return self._get("snapshots", snapshot_id)

    def record_snapshot(self, snapshot: Snapshot, content: bytes, actor: str) -> Snapshot:
        if hashlib.sha256(content).hexdigest() != snapshot.sha256:
            raise ValueError("Snapshot sha256 does not match the content")
        with self._txn():
            if snapshot.id in self._s["snapshots"]:
                raise ValueError(f"Snapshot {snapshot.id} already exists")
            self._s["snapshots"][snapshot.id] = snapshot.model_copy(deep=True)
            self._blobs.setdefault(snapshot.sha256, content)  # write-once
            self._audit(actor, "snapshot.recorded", "snapshot", snapshot.id, None, _dump(snapshot))
            return snapshot.model_copy(deep=True)

    def add_raw_items(self, items: list[RawListingItem], actor: str) -> list[RawListingItem]:
        if not items:
            return []
        with self._txn():
            for item in items:
                self._s["raw_items"][item.id] = item.model_copy(deep=True)
            first = items[0]
            self._audit(
                actor,
                "raw_listing_items.recorded",
                "crawl_run",
                first.run_id,
                None,
                {"source_id": first.source_id, "channel": first.channel.value, "count": len(items)},
            )
            return [i.model_copy(deep=True) for i in items]

    def list_raw_items(self, run_id: str, source_id: str | None = None) -> list[RawListingItem]:
        rows = [r for r in self._all("raw_items") if r.run_id == run_id]
        if source_id is not None:
            rows = [r for r in rows if r.source_id == source_id]
        return sorted(rows, key=lambda r: (r.fetched_at, r.id))

    # --- items, evidence, decisions ----------------------------------------------------------

    def list_items(self, f: ItemFilter) -> list[RegulatoryItem]:
        items = sorted(self._all("items"), key=lambda i: (i.first_seen_at, i.id))
        if f.cycle is not None:
            run_ids = {r.id for r in self.find_runs(f.cycle)}
            in_cycle = {o.item_id for o in self._s["observations"].values() if o.run_id in run_ids}
            items = [i for i in items if i.id in in_cycle]
        if f.state is not None:
            items = [i for i in items if i.state == f.state]
        if f.channel is not None:
            items = [i for i in items if i.channel == f.channel]
        if f.min_relevance is not None:
            items = [i for i in items if i.relevance_score >= f.min_relevance]
        if f.q:
            needle = f.q.strip().lower()
            sources = self._s["sources"]

            def haystack(i: RegulatoryItem) -> str:
                authority = sources[i.source_id].authority if i.source_id in sources else ""
                return " ".join(
                    [i.title, i.ref_no or "", i.internal_ref, authority, i.summary or ""]
                ).lower()

            items = [i for i in items if needle in haystack(i)]
        return items

    def get_item(self, item_id: str) -> RegulatoryItem | None:
        return self._get("items", item_id)

    def _ordered_observations(self, keep: Any) -> list[ItemObservation]:
        started = {r.id: r.started_at for r in self._s["runs"].values()}
        found = [o for o in self._all("observations") if keep(o) and o.run_id in started]
        return sorted(found, key=lambda o: (started[o.run_id], o.id))

    def list_observations(self, item_id: str) -> list[ItemObservation]:
        return self._ordered_observations(lambda o: o.item_id == item_id)

    def list_observations_for_runs(self, run_ids: list[str]) -> list[ItemObservation]:
        wanted = set(run_ids)
        return self._ordered_observations(lambda o: o.run_id in wanted)

    def list_documents(self, item_id: str) -> list[Document]:
        docs = [d for d in self._all("documents") if d.item_id == item_id]
        return sorted(docs, key=lambda d: (d.fetched_at is None, d.fetched_at or now_ist(), d.id))

    def get_document(self, document_id: str) -> Document | None:
        return self._get("documents", document_id)

    def add_document(
        self,
        doc: Document,
        content: bytes | None,
        new_state: ItemState | None,
        actor: str,
        text: str | None = None,
    ) -> Document:
        with self._txn():
            self._s["documents"][doc.id] = doc.model_copy(deep=True)
            if content is not None and doc.sha256:
                self._blobs[doc.sha256] = content
            if text is not None and doc.text_path:
                self._texts[doc.text_path] = text
            self._audit(actor, "document.added", "document", doc.id, None, _dump(doc))
            if new_state is not None:
                self._set_state(doc.item_id, new_state, actor)
            return doc.model_copy(deep=True)

    def read_text(self, document_id: str) -> str | None:
        doc = self._s["documents"].get(document_id)
        return None if doc is None or not doc.text_path else self._texts.get(doc.text_path)

    def find_item_by_fingerprint(self, fingerprint: str) -> RegulatoryItem | None:
        for item in self._s["items"].values():
            if item.fingerprint == fingerprint:
                return item.model_copy(deep=True)
        return None

    def save_detection(
        self,
        item: RegulatoryItem,
        observations: list[ItemObservation],
        is_new: bool,
        actor: str,
    ) -> RegulatoryItem:
        with self._txn():
            if is_new:
                year = item.first_seen_at.year
                prefix = f"TVSC-REG-{year:04d}-"
                taken = [
                    int(i.internal_ref[len(prefix) :])
                    for i in self._s["items"].values()
                    if i.internal_ref.startswith(prefix)
                ]
                item = item.model_copy(
                    update={"internal_ref": f"{prefix}{max(taken, default=0) + 1:06d}"}
                )
                if any(i.fingerprint == item.fingerprint for i in self._s["items"].values()):
                    raise ValueError(f"Item with fingerprint {item.fingerprint} already exists")
                before = None
            else:
                before = _dump(self._s["items"][item.id])
            self._s["items"][item.id] = item.model_copy(deep=True)
            for obs in observations:
                self._s["observations"][obs.id] = obs.model_copy(deep=True)
            action = "regulatory_item.detected" if is_new else "regulatory_item.updated"
            self._audit(actor, action, "regulatory_item", item.id, before, _dump(item))
            return item.model_copy(deep=True)

    def mark_items_seen(self, item_ids: list[str], seen_at: datetime, actor: str) -> None:
        if not item_ids:
            return
        with self._txn():
            for item_id in item_ids:
                self._s["items"][item_id].last_seen_at = seen_at
            self._audit(
                actor,
                "regulatory_items.seen",
                "regulatory_item",
                item_ids[0],
                None,
                {"count": len(item_ids), "seen_at": seen_at.isoformat()},
            )

    def _set_state(self, item_id: str, state: ItemState, actor: str) -> None:
        item: RegulatoryItem = self._s["items"][item_id]
        if item.state == state:
            return
        before = {"state": item.state.value}
        item.state = state
        self._audit(
            actor,
            "regulatory_item.state_changed",
            "regulatory_item",
            item_id,
            before,
            {"state": state.value},
        )

    def list_decisions(self, item_id: str) -> list[ReviewDecision]:
        decisions = [d for d in self._all("decisions") if d.item_id == item_id]
        return sorted(decisions, key=lambda d: (d.decided_at, d.id))

    def save_decisions(
        self, decisions: list[ReviewDecision], new_state: ItemState, actor: str
    ) -> list[ReviewDecision]:
        with self._txn():
            saved: list[ReviewDecision] = []
            for d in decisions:
                if d.item_id not in self._s["items"]:
                    raise KeyError(d.item_id)
                self._s["decisions"][d.id] = d.model_copy(deep=True)
                if d.is_draft:
                    action = "review_decision.saved"
                elif d.dismiss_reason:
                    action = "review_decision.dismissed"
                else:
                    action = "review_decision.accepted"
                self._audit(actor, action, "review_decision", d.id, None, _dump(d))
                self._set_state(d.item_id, new_state, actor)
                saved.append(d.model_copy(deep=True))
            return saved

    # --- daily update ------------------------------------------------------------------------

    def find_daily_update(self, ref: CycleRef) -> DailyUpdate | None:
        for u in self._s["daily_updates"].values():
            if u.cycle_date == ref.cycle_date and u.cycle == ref.cycle:
                return u.model_copy(deep=True)
        return None

    def get_daily_update(self, update_id: str) -> DailyUpdate | None:
        return self._get("daily_updates", update_id)

    def list_daily_update_items(self, update_id: str) -> list[DailyUpdateItem]:
        rows = self._update_rows.get(update_id, [])
        return sorted((r.model_copy() for r in rows), key=lambda r: r.serial_no)

    def list_daily_updates_for_item(self, item_id: str) -> list[DailyUpdate]:
        ids = {
            uid
            for uid, rows in self._update_rows.items()
            if any(r.item_id == item_id for r in rows)
        }
        found = [u for u in self._all("daily_updates") if u.id in ids]
        return sorted(found, key=lambda u: (u.cycle_date, u.id))

    def save_daily_update(
        self, update: DailyUpdate, rows: list[DailyUpdateItem], actor: str
    ) -> DailyUpdate:
        with self._txn():
            existing = self._s["daily_updates"].get(update.id)
            if existing is not None and existing.status != UpdateStatus.DRAFT:
                raise ValueError("Only a draft can be changed")
            before = (
                None
                if existing is None
                else {
                    "update": _dump(existing),
                    "item_ids": [r.item_id for r in self._update_rows.get(update.id, [])],
                }
            )
            self._s["daily_updates"][update.id] = update.model_copy(deep=True)
            self._update_rows[update.id] = [r.model_copy() for r in rows]
            action = "daily_update.drafted" if existing is None else "daily_update.changed"
            self._audit(
                actor,
                action,
                "daily_update",
                update.id,
                before,
                {"update": _dump(update), "item_ids": [r.item_id for r in rows]},
            )
            return update.model_copy(deep=True)

    def approve_daily_update(self, update_id: str, approver: str, actor: str) -> DailyUpdate:
        with self._txn():
            update: DailyUpdate = self._s["daily_updates"][update_id]
            if update.status != UpdateStatus.DRAFT:
                raise ValueError("Only a draft can be approved")
            before = _dump(update)
            update.status = UpdateStatus.APPROVED
            update.approved_by = approver
            update.approved_at = now_ist()
            self._audit(
                actor, "daily_update.approved", "daily_update", update_id, before, _dump(update)
            )
            for row in self._update_rows.get(update_id, []):
                self._set_state(row.item_id, ItemState.PUBLISHED, actor)
            return update.model_copy(deep=True)

    def mark_daily_update_exported(self, update_id: str, actor: str) -> DailyUpdate:
        with self._txn():
            update: DailyUpdate = self._s["daily_updates"][update_id]
            if update.status == UpdateStatus.DRAFT:
                raise ValueError("Approve the update before exporting")
            if update.status == UpdateStatus.EXPORTED:
                return update.model_copy(deep=True)
            before = _dump(update)
            update.status = UpdateStatus.EXPORTED
            update.exported_at = now_ist()
            self._audit(
                actor, "daily_update.exported", "daily_update", update_id, before, _dump(update)
            )
            return update.model_copy(deep=True)

    # --- configuration -----------------------------------------------------------------------

    # SME routing, recipients and the email wording are versioned settings (maker-checker like
    # every other setting). These three methods are read-only views of the approved values.
    def list_sme_functions(self) -> list[SmeFunction]:
        s = self._settings["sme-routing"]
        return [SmeFunction(**item, version=s.version) for item in copy.deepcopy(s.value)]

    def list_recipients(self) -> list[Recipient]:
        s = self._settings["recipients"]
        return [Recipient(**item, version=s.version) for item in copy.deepcopy(s.value)]

    def get_email_template(self) -> EmailTemplate:
        s = self._settings["email-template"]
        return EmailTemplate(id="tpl-daily-update", version=s.version, **copy.deepcopy(s.value))

    def list_settings(self) -> list[Setting]:
        return sorted(
            (s.model_copy(deep=True) for s in self._settings.values()), key=lambda s: s.key
        )

    def get_setting(self, key: str) -> Setting | None:
        s = self._settings.get(key)
        return None if s is None else s.model_copy(deep=True)

    def propose_setting(self, key: str, value: Any, requested_by: str, actor: str) -> Setting:
        with self._txn():
            setting = self._settings[key]
            before = _dump(setting)
            setting.pending = SettingChange(
                value=value,
                requested_by=requested_by,
                requested_at=now_ist(),
                version=setting.version + 1,
            )
            self._audit(actor, "setting.change_requested", "setting", key, before, _dump(setting))
            return setting.model_copy(deep=True)

    def approve_setting(self, key: str, approver: str, actor: str) -> Setting:
        with self._txn():
            setting = self._settings[key]
            if setting.pending is None:
                raise ValueError("Nothing is waiting for approval")
            before = _dump(setting)
            pending = setting.pending
            setting.value = pending.value
            setting.version = pending.version
            setting.updated_by = pending.requested_by
            setting.updated_at = now_ist()
            setting.pending = None
            self._audit(
                actor,
                "setting.change_approved",
                "setting",
                key,
                before,
                {**_dump(setting), "approved_by": approver},
            )
            return setting.model_copy(deep=True)

    def reject_setting(self, key: str, reason: str, actor: str) -> Setting:
        with self._txn():
            setting = self._settings[key]
            if setting.pending is None:
                raise ValueError("Nothing is waiting for approval")
            before = _dump(setting)
            setting.pending = None
            self._audit(
                actor,
                "setting.change_rejected",
                "setting",
                key,
                before,
                {**_dump(setting), "reason": reason},
            )
            return setting.model_copy(deep=True)

    # --- audit -------------------------------------------------------------------------------

    def list_audit(
        self,
        entity: str | None = None,
        actor: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[AuditEvent]:
        events = self._all("audit")
        if entity:
            events = [e for e in events if entity in (e.entity, e.entity_id)]
        if actor:
            events = [e for e in events if e.actor.lower() == actor.lower()]
        if date_from:
            events = [e for e in events if e.ts.date() >= date_from]
        if date_to:
            events = [e for e in events if e.ts.date() <= date_to]
        return sorted(events, key=lambda e: (e.ts, e.id), reverse=True)
