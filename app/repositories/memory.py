"""In-memory Repository for the mock-data milestone (REPO_BACKEND=memory).

Behaves like the future SQL repository: every mutation runs in a "transaction" (rolled back if
anything fails) and writes its AuditEvent inside it. Objects are copied on the way in and out so
callers cannot change stored state, and snapshots/documents/audit events have no update or delete.
"""

import copy
import threading
import uuid
from collections.abc import Iterable, Iterator
from contextlib import contextmanager
from datetime import date
from typing import Any

from pydantic import BaseModel

from app.schemas.api import CycleRef, ItemFilter
from app.schemas.entities import (
    AppUser,
    AuditEvent,
    CrawlRun,
    DailyUpdate,
    DailyUpdateItem,
    Document,
    EmailTemplate,
    ItemObservation,
    ManualCheck,
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
from app.schemas.enums import Cycle, ItemState, RunStatus, UpdateStatus, VersionStatus

_COLLECTIONS: dict[type[BaseModel], str] = {
    AppUser: "users",
    Source: "sources",
    SourceVersion: "source_versions",
    CrawlRun: "runs",
    SourceCheck: "checks",
    Snapshot: "snapshots",
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

    # --- internals ---------------------------------------------------------------------------

    @contextmanager
    def _txn(self) -> Iterator[None]:
        """All-or-nothing: restore the previous state if the block raises."""
        with self._lock:
            backup = copy.deepcopy((self._s, self._settings, self._update_rows, self._blobs))
            try:
                yield
            except BaseException:
                self._s, self._settings, self._update_rows, self._blobs = backup
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
        return self._all("users")

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

    def create_run(self, cycle: Cycle, cycle_date: date, triggered_by: str, actor: str) -> CrawlRun:
        with self._txn():
            run = CrawlRun(
                id=new_id("run"),
                cycle_date=cycle_date,
                cycle=cycle,
                triggered_by=triggered_by,
                started_at=now_ist(),
                status=RunStatus.RUNNING,
            )
            self._s["runs"][run.id] = run
            self._audit(actor, "crawl_run.created", "crawl_run", run.id, None, _dump(run))
            return run.model_copy(deep=True)

    def list_source_checks(self, run_id: str | None = None) -> list[SourceCheck]:
        checks = self._all("checks")
        if run_id is not None:
            checks = [c for c in checks if c.run_id == run_id]
        return checks

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

    # --- items, evidence, decisions ----------------------------------------------------------

    def list_items(self, f: ItemFilter) -> list[RegulatoryItem]:
        items = self._all("items")
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

    def list_observations(self, item_id: str) -> list[ItemObservation]:
        return [o for o in self._all("observations") if o.item_id == item_id]

    def list_observations_for_runs(self, run_ids: list[str]) -> list[ItemObservation]:
        wanted = set(run_ids)
        return [o for o in self._all("observations") if o.run_id in wanted]

    def list_documents(self, item_id: str) -> list[Document]:
        return [d for d in self._all("documents") if d.item_id == item_id]

    def add_document(
        self, doc: Document, content: bytes | None, new_state: ItemState | None, actor: str
    ) -> Document:
        with self._txn():
            self._s["documents"][doc.id] = doc.model_copy(deep=True)
            if content is not None and doc.sha256:
                self._blobs[doc.sha256] = content
            self._audit(actor, "document.added", "document", doc.id, None, _dump(doc))
            if new_state is not None:
                self._set_state(doc.item_id, new_state, actor)
            return doc.model_copy(deep=True)

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
        return sorted(decisions, key=lambda d: d.decided_at)

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
        return [u for u in self._all("daily_updates") if u.id in ids]

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

    def list_sme_functions(self) -> list[SmeFunction]:
        return self._all("sme_functions")

    def list_recipients(self) -> list[Recipient]:
        return self._all("recipients")

    def get_email_template(self) -> EmailTemplate:
        templates = self._all("email_templates")
        return max(templates, key=lambda t: t.version)

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
        return sorted(events, key=lambda e: e.ts, reverse=True)
