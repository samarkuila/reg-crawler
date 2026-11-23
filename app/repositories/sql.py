"""SQL Repository (REPO_BACKEND=sql): SQLite for the POC, PostgreSQL by configuration.

Same contract as the in-memory repository (tests/contract runs both):
- every mutating method runs in ONE transaction together with its audit event, so a failure leaves
  neither behind;
- snapshots, documents and audit events are only ever inserted (the database also refuses
  UPDATE/DELETE on them);
- datetimes come back in IST, exactly like the in-memory repository;
- SME routing, recipients and the email wording are read from the approved `setting` values
  (docs/data-model.md, deviation 3), so the three config tables are not used.

Stored copies (snapshot and document files) live under FILES_ROOT at the `path` recorded in the
table, written once and never overwritten.
"""

import copy
import hashlib
import os
import re
import uuid
from collections.abc import Iterable
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy import JSON, Engine, and_, delete, func, or_, select
from sqlalchemy.orm import Session, sessionmaker

from app.db import models as m
from app.db.refs import PREFIX, next_internal_ref
from app.db.session import make_session_factory, session_scope
from app.schemas.api import CycleRef, ItemFilter
from app.schemas.entities import (
    IST,
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

_ENTITY_TO_MODEL: dict[type[BaseModel], type[m.Base]] = {
    AppUser: m.AppUser,
    Source: m.Source,
    SourceVersion: m.SourceVersion,
    CrawlRun: m.CrawlRun,
    SourceCheck: m.SourceCheck,
    Snapshot: m.Snapshot,
    RawListingItem: m.RawListingItem,
    Alert: m.Alert,
    AiExtraction: m.AiExtraction,
    AgentRun: m.AgentRun,
    AgentFlag: m.AgentFlag,
    RegulatoryItem: m.RegulatoryItem,
    ItemObservation: m.ItemObservation,
    Document: m.Document,
    ReviewDecision: m.ReviewDecision,
    DailyUpdate: m.DailyUpdate,
    DailyUpdateItem: m.DailyUpdateItem,
    ManualCheck: m.ManualCheck,
    Setting: m.Setting,
    AuditEvent: m.AuditEvent,
}
_ROLE_RANK = {"reviewer": 0, "approver": 1, "admin": 2, "viewer": 3}
_REF = re.compile(rf"^{PREFIX}-(\d{{4}})-(\d{{6,}})$")


def new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _dump(obj: BaseModel | None) -> Any:
    return None if obj is None else obj.model_dump(mode="json")


def _ist(value: Any) -> Any:
    """The database returns UTC; the application works in IST."""
    return value.astimezone(IST) if isinstance(value, datetime) and value.tzinfo else value


def _to_entity[E: BaseModel](cls: type[E], row: m.Base) -> E:
    data = {name: _ist(getattr(row, name, None)) for name in cls.model_fields if hasattr(row, name)}
    return cls.model_validate(copy.deepcopy(data))


def _row_values(model: type[m.Base], entity: BaseModel) -> dict[str, Any]:
    """Column values for an entity: JSON columns get JSON-safe values, the rest keep their types."""
    python, jsonable = entity.model_dump(), entity.model_dump(mode="json")
    out: dict[str, Any] = {}
    for column in model.__table__.columns:  # type: ignore[attr-defined]
        name = column.key
        if name not in python:
            continue
        out[name] = jsonable[name] if isinstance(column.type, JSON) else python[name]
    return out


def _to_row(model: type[m.Base], entity: BaseModel, **extra: Any) -> m.Base:
    return model(**_row_values(model, entity), **extra)


class SqlRepository:
    def __init__(self, engine: Engine, files_root: str | os.PathLike[str] | None = None) -> None:
        self._engine = engine
        self._factory: sessionmaker[Session] = make_session_factory(engine)
        self._root = Path(files_root or os.environ.get("FILES_ROOT") or ".").resolve()

    # --- internals ---------------------------------------------------------------------------

    def _tx(self):  # type: ignore[no-untyped-def]
        """One transaction: committed when the block ends, rolled back if anything raises."""
        return session_scope(self._factory)

    def _audit(
        self,
        session: Session,
        actor: str,
        action: str,
        entity: str,
        entity_id: str,
        before: Any,
        after: Any,
    ) -> None:
        session.add(
            m.AuditEvent(
                id=new_id("aud"),
                ts=now_ist(),
                actor=actor,
                action=action,
                entity=entity,
                entity_id=entity_id,
                before=before,
                after=after,
            )
        )

    def _file(self, relative: str) -> Path:
        path = (self._root / relative).resolve()
        if self._root not in path.parents:
            raise ValueError(f"Stored copy path escapes the data folder: {relative}")
        return path

    def _write_once(self, relative: str, content: bytes) -> None:
        path = self._file(relative)
        if path.exists():
            return  # content-addressed and immutable: never overwritten
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + f".{uuid.uuid4().hex[:6]}.tmp")
        tmp.write_bytes(content)
        os.replace(tmp, path)

    def _blob_path(self, session: Session, sha256: str) -> str | None:
        for model in (m.Snapshot, m.Document):
            found = session.scalars(
                select(model.path).where(model.sha256 == sha256, model.path.is_not(None)).limit(1)
            ).first()
            if found:
                return found
        return None

    # --- seed loader and stored copies ---------------------------------------------------------

    def load(self, entities: Iterable[BaseModel]) -> None:
        """Seed loader: inserts entities without audit events (seed data is not a user action).

        Parents must come before the rows that point at them, as in `seed.load_seed`.
        """
        with self._tx() as session:
            next_position = (session.scalar(select(func.max(m.Source.position))) or 0) + 1
            for obj in entities:
                if isinstance(obj, Source):
                    session.add(_to_row(m.Source, obj, position=next_position))
                    next_position += 1
                    session.flush()
                    continue
                model = _ENTITY_TO_MODEL[type(obj)]
                session.add(_to_row(model, obj))
                session.flush()
            self._sync_ref_counters(session)

    def _sync_ref_counters(self, session: Session) -> None:
        """New references continue after the highest one already stored for each year."""
        highest: dict[int, int] = {}
        for (ref,) in session.execute(select(m.RegulatoryItem.internal_ref)):
            match = _REF.match(ref)
            if match:
                year, number = int(match.group(1)), int(match.group(2))
                highest[year] = max(highest.get(year, 0), number)
        for year, number in highest.items():
            counter = session.get(m.InternalRefCounter, year)
            if counter is None:
                session.add(m.InternalRefCounter(year=year, last_value=number))
            elif counter.last_value < number:
                counter.last_value = number
        session.flush()

    def is_empty(self) -> bool:
        with self._tx() as session:
            return session.scalar(select(func.count()).select_from(m.AppUser)) == 0 and (
                session.scalar(select(func.count()).select_from(m.Source)) == 0
            )

    def load_blob(self, sha256: str, content: bytes) -> None:
        """Seed loader for stored copies: written where the snapshot/document row says."""
        with self._tx() as session:
            relative = self._blob_path(session, sha256)
        if relative is None:
            raise KeyError(f"No snapshot or document has sha256 {sha256}")
        self._write_once(relative, content)

    def read_blob(self, sha256: str) -> bytes | None:
        with self._tx() as session:
            relative = self._blob_path(session, sha256)
        if relative is None:
            return None
        path = self._file(relative)
        return path.read_bytes() if path.is_file() else None

    # --- users -------------------------------------------------------------------------------

    def list_users(self) -> list[AppUser]:
        with self._tx() as session:
            users = [_to_entity(AppUser, r) for r in session.scalars(select(m.AppUser))]
        return sorted(users, key=lambda u: (_ROLE_RANK[u.role.value], u.name))

    def get_user(self, user_id: str) -> AppUser | None:
        with self._tx() as session:
            row = session.get(m.AppUser, user_id)
            return None if row is None else _to_entity(AppUser, row)

    # --- sources and versions ----------------------------------------------------------------

    def list_sources(self) -> list[Source]:
        with self._tx() as session:
            rows = session.scalars(select(m.Source).order_by(m.Source.position, m.Source.id))
            return [_to_entity(Source, r) for r in rows]

    def get_source(self, source_id: str) -> Source | None:
        with self._tx() as session:
            row = session.get(m.Source, source_id)
            return None if row is None else _to_entity(Source, row)

    def create_source(self, source: Source, version: SourceVersion, actor: str) -> Source:
        with self._tx() as session:
            if session.get(m.Source, source.id) is not None:
                raise ValueError(f"Source {source.id} already exists")
            position = (session.scalar(select(func.max(m.Source.position))) or 0) + 1
            session.add(_to_row(m.Source, source, position=position))
            session.add(_to_row(m.SourceVersion, version))
            self._audit(session, actor, "source.created", "source", source.id, None, _dump(source))
            self._audit(
                session,
                actor,
                "source_version.requested",
                "source_version",
                version.id,
                None,
                _dump(version),
            )
            return source.model_copy(deep=True)

    def list_source_versions(self, source_id: str) -> list[SourceVersion]:
        with self._tx() as session:
            rows = session.scalars(
                select(m.SourceVersion)
                .where(m.SourceVersion.source_id == source_id)
                .order_by(m.SourceVersion.version)
            )
            return [_to_entity(SourceVersion, r) for r in rows]

    def get_source_version(self, source_id: str, version: int) -> SourceVersion | None:
        with self._tx() as session:
            row = session.scalars(
                select(m.SourceVersion).where(
                    m.SourceVersion.source_id == source_id, m.SourceVersion.version == version
                )
            ).first()
            return None if row is None else _to_entity(SourceVersion, row)

    def add_source_version(
        self, source_id: str, after: dict[str, Any], actor: str
    ) -> SourceVersion:
        with self._tx() as session:
            row = session.get(m.Source, source_id)
            if row is None:
                raise KeyError(source_id)
            current = _to_entity(Source, row).model_dump(mode="json")
            top = session.scalar(
                select(func.max(m.SourceVersion.version)).where(
                    m.SourceVersion.source_id == source_id
                )
            )
            ver = SourceVersion(
                id=new_id("srcv"),
                source_id=source_id,
                version=(top or 0) + 1,
                before={k: current.get(k) for k in after},
                after=after,
                status=VersionStatus.PENDING,
                requested_by=actor,
                ts=now_ist(),
            )
            session.add(_to_row(m.SourceVersion, ver))
            self._audit(
                session,
                actor,
                "source_version.requested",
                "source_version",
                ver.id,
                None,
                _dump(ver),
            )
            return ver.model_copy(deep=True)

    def _find_version(self, session: Session, source_id: str, version: int) -> m.SourceVersion:
        row = session.scalars(
            select(m.SourceVersion).where(
                m.SourceVersion.source_id == source_id, m.SourceVersion.version == version
            )
        ).first()
        if row is None:
            raise KeyError(f"{source_id} v{version}")
        return row

    def approve_source_version(
        self, source_id: str, version: int, approver: str, actor: str
    ) -> SourceVersion:
        with self._tx() as session:
            ver_row = self._find_version(session, source_id, version)
            if ver_row.status != VersionStatus.PENDING:
                raise ValueError("Only a pending version can be approved")
            source_row = session.get(m.Source, source_id)
            assert source_row is not None  # noqa: S101 - foreign key guarantees it
            before_source = _dump(_to_entity(Source, source_row))
            before_ver = _dump(_to_entity(SourceVersion, ver_row))
            changes = dict(ver_row.after)
            if changes.get("active"):
                changes["planned"] = False
            merged = Source.model_validate(
                {
                    **_to_entity(Source, source_row).model_dump(),
                    **changes,
                    "current_version": version,
                }
            )
            for name, value in _row_values(m.Source, merged).items():
                if name != "id":
                    setattr(source_row, name, value)
            ver_row.status = VersionStatus.APPROVED
            ver_row.approved_by = approver
            ver_row.decided_at = now_ist()
            session.flush()
            ver = _to_entity(SourceVersion, ver_row)
            self._audit(
                session,
                actor,
                "source_version.approved",
                "source_version",
                ver.id,
                before_ver,
                _dump(ver),
            )
            self._audit(
                session,
                actor,
                "source.updated",
                "source",
                source_id,
                before_source,
                _dump(_to_entity(Source, source_row)),
            )
            return ver

    def reject_source_version(
        self, source_id: str, version: int, approver: str, reason: str, actor: str
    ) -> SourceVersion:
        with self._tx() as session:
            ver_row = self._find_version(session, source_id, version)
            if ver_row.status != VersionStatus.PENDING:
                raise ValueError("Only a pending version can be rejected")
            before_ver = _dump(_to_entity(SourceVersion, ver_row))
            ver_row.status = VersionStatus.REJECTED
            ver_row.approved_by = approver
            ver_row.decided_at = now_ist()
            ver_row.reject_reason = reason
            session.flush()
            ver = _to_entity(SourceVersion, ver_row)
            self._audit(
                session,
                actor,
                "source_version.rejected",
                "source_version",
                ver.id,
                before_ver,
                _dump(ver),
            )
            return ver

    # --- runs, checks, snapshots -------------------------------------------------------------

    def list_runs(self, cycle_date: date | None = None) -> list[CrawlRun]:
        with self._tx() as session:
            stmt = select(m.CrawlRun).order_by(m.CrawlRun.started_at.desc(), m.CrawlRun.id)
            if cycle_date is not None:
                stmt = stmt.where(m.CrawlRun.cycle_date == cycle_date)
            return [_to_entity(CrawlRun, r) for r in session.scalars(stmt)]

    def list_cycles(self) -> list[CycleRef]:
        seen: list[CycleRef] = []
        for run in self.list_runs():
            ref = CycleRef(cycle_date=run.cycle_date, cycle=run.cycle)
            if ref not in seen:
                seen.append(ref)
        return seen

    def get_run(self, run_id: str) -> CrawlRun | None:
        with self._tx() as session:
            row = session.get(m.CrawlRun, run_id)
            return None if row is None else _to_entity(CrawlRun, row)

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
        with self._tx() as session:
            run = CrawlRun(
                id=new_id("run"),
                cycle_date=cycle_date,
                cycle=cycle,
                triggered_by=triggered_by,
                started_at=now_ist(),
                status=RunStatus.RUNNING,
                sources=list(sources) if sources is not None else None,
            )
            session.add(_to_row(m.CrawlRun, run))
            self._audit(session, actor, "crawl_run.created", "crawl_run", run.id, None, _dump(run))
            return run.model_copy(deep=True)

    # --- Luna agent ----------------------------------------------------------------------------

    def save_extraction(self, ext: AiExtraction, enrich: bool, actor: str) -> AiExtraction:
        with self._tx() as session:
            if session.get(m.RegulatoryItem, ext.item_id) is None:
                raise KeyError(ext.item_id)
            session.add(_to_row(m.AiExtraction, ext))
            self._audit(
                session, actor, "ai_extraction.saved", "ai_extraction", ext.id, None, _dump(ext)
            )
            session.flush()
            row = session.get(m.RegulatoryItem, ext.item_id)
            if enrich and row is not None and row.state == ItemState.DETECTED:
                self._set_state(session, ext.item_id, ItemState.ENRICHED, actor)
            return ext.model_copy(deep=True)

    def list_extractions(self, item_id: str) -> list[AiExtraction]:
        with self._tx() as session:
            stmt = (
                select(m.AiExtraction)
                .where(m.AiExtraction.item_id == item_id)
                .order_by(m.AiExtraction.created_at, m.AiExtraction.id)
            )
            return [_to_entity(AiExtraction, r) for r in session.scalars(stmt)]

    def add_agent_run(self, run: AgentRun, actor: str) -> AgentRun:
        with self._tx() as session:
            session.add(_to_row(m.AgentRun, run))
            self._audit(session, actor, "agent_run.logged", "agent_run", run.id, None, _dump(run))
            return run.model_copy(deep=True)

    def list_agent_runs(self, day: date | None = None) -> list[AgentRun]:
        with self._tx() as session:
            stmt = select(m.AgentRun).order_by(m.AgentRun.created_at, m.AgentRun.id)
            runs = [_to_entity(AgentRun, r) for r in session.scalars(stmt)]
        return [r for r in runs if day is None or r.created_at.date() == day]

    def add_agent_flags(self, flags: list[AgentFlag], actor: str) -> list[AgentFlag]:
        if not flags:
            return []
        with self._tx() as session:
            for flag in flags:
                session.add(_to_row(m.AgentFlag, flag))
            self._audit(
                session,
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
        with self._tx() as session:
            stmt = select(m.AgentFlag).order_by(m.AgentFlag.created_at, m.AgentFlag.id)
            if source_id is not None:
                stmt = stmt.where(m.AgentFlag.source_id == source_id)
            if run_id is not None:
                stmt = stmt.where(m.AgentFlag.run_id == run_id)
            if item_id is not None:
                stmt = stmt.where(m.AgentFlag.item_id == item_id)
            if status is not None:
                stmt = stmt.where(m.AgentFlag.status == status)
            return [_to_entity(AgentFlag, r) for r in session.scalars(stmt)]

    def resolve_agent_flag(
        self, flag_id: str, status: FlagStatus, resolved_by: str, actor: str
    ) -> AgentFlag:
        with self._tx() as session:
            row = session.get(m.AgentFlag, flag_id)
            if row is None:
                raise KeyError(flag_id)
            before = _dump(_to_entity(AgentFlag, row))
            row.status, row.resolved_by, row.resolved_at = status, resolved_by, now_ist()
            session.flush()
            after = _to_entity(AgentFlag, row)
            self._audit(
                session, actor, "agent_flag.resolved", "agent_flag", flag_id, before, _dump(after)
            )
            return after

    def close_run(self, run_id: str, status: RunStatus, actor: str) -> CrawlRun:
        with self._tx() as session:
            row = session.get(m.CrawlRun, run_id)
            if row is None:
                raise KeyError(run_id)
            before = _dump(_to_entity(CrawlRun, row))
            row.status = status
            row.ended_at = now_ist()
            session.flush()
            after = _to_entity(CrawlRun, row)
            self._audit(
                session, actor, "crawl_run.closed", "crawl_run", run_id, before, _dump(after)
            )
            return after

    def add_alerts(self, alerts: list[Alert], actor: str) -> list[Alert]:
        if not alerts:
            return []
        with self._tx() as session:
            for alert in alerts:
                session.add(_to_row(m.Alert, alert))
            self._audit(
                session,
                actor,
                "alerts.recorded",
                "crawl_run",
                alerts[0].run_id or alerts[0].id,
                None,
                {"count": len(alerts), "kinds": sorted({a.kind.value for a in alerts})},
            )
            return [a.model_copy(deep=True) for a in alerts]

    def list_alerts(self, run_id: str | None = None) -> list[Alert]:
        with self._tx() as session:
            stmt = select(m.Alert).order_by(m.Alert.created_at, m.Alert.id)
            if run_id is not None:
                stmt = stmt.where(m.Alert.run_id == run_id)
            return [_to_entity(Alert, r) for r in session.scalars(stmt)]

    def list_source_checks(self, run_id: str | None = None) -> list[SourceCheck]:
        with self._tx() as session:
            stmt = select(m.SourceCheck).order_by(m.SourceCheck.id)
            if run_id is not None:
                stmt = stmt.where(m.SourceCheck.run_id == run_id)
            return [_to_entity(SourceCheck, r) for r in session.scalars(stmt)]

    def get_source_check(self, check_id: str) -> SourceCheck | None:
        with self._tx() as session:
            row = session.get(m.SourceCheck, check_id)
            return None if row is None else _to_entity(SourceCheck, row)

    def record_source_check(self, check: SourceCheck, actor: str) -> SourceCheck:
        with self._tx() as session:
            session.merge(_to_row(m.SourceCheck, check))
            self._audit(
                session,
                actor,
                "source_check.recorded",
                "source_check",
                check.id,
                None,
                _dump(check),
            )
            return check.model_copy(deep=True)

    def get_manual_check(self, source_check_id: str) -> ManualCheck | None:
        with self._tx() as session:
            row = session.scalars(
                select(m.ManualCheck).where(m.ManualCheck.source_check_id == source_check_id)
            ).first()
            return None if row is None else _to_entity(ManualCheck, row)

    def add_manual_check(
        self, source_check_id: str, user: str, note: str, actor: str
    ) -> ManualCheck:
        with self._tx() as session:
            if session.get(m.SourceCheck, source_check_id) is None:
                raise KeyError(source_check_id)
            check = ManualCheck(
                id=new_id("mchk"),
                source_check_id=source_check_id,
                user=user,
                note=note,
                ts=now_ist(),
            )
            session.add(_to_row(m.ManualCheck, check))
            self._audit(
                session, actor, "manual_check.added", "manual_check", check.id, None, _dump(check)
            )
            return check.model_copy(deep=True)

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        with self._tx() as session:
            row = session.get(m.Snapshot, snapshot_id)
            return None if row is None else _to_entity(Snapshot, row)

    def record_snapshot(self, snapshot: Snapshot, content: bytes, actor: str) -> Snapshot:
        if hashlib.sha256(content).hexdigest() != snapshot.sha256:
            raise ValueError("Snapshot sha256 does not match the content")
        with self._tx() as session:
            session.add(_to_row(m.Snapshot, snapshot))
            self._audit(
                session, actor, "snapshot.recorded", "snapshot", snapshot.id, None, _dump(snapshot)
            )
            session.flush()
            self._write_once(snapshot.path, content)  # a failed write rolls the row back
            return snapshot.model_copy(deep=True)

    def add_raw_items(self, items: list[RawListingItem], actor: str) -> list[RawListingItem]:
        if not items:
            return []
        with self._tx() as session:
            for item in items:
                session.add(_to_row(m.RawListingItem, item))
            first = items[0]
            self._audit(
                session,
                actor,
                "raw_listing_items.recorded",
                "crawl_run",
                first.run_id,
                None,
                {"source_id": first.source_id, "channel": first.channel.value, "count": len(items)},
            )
            return [i.model_copy(deep=True) for i in items]

    def list_raw_items(self, run_id: str, source_id: str | None = None) -> list[RawListingItem]:
        with self._tx() as session:
            stmt = select(m.RawListingItem).where(m.RawListingItem.run_id == run_id)
            if source_id is not None:
                stmt = stmt.where(m.RawListingItem.source_id == source_id)
            rows = session.scalars(stmt.order_by(m.RawListingItem.fetched_at, m.RawListingItem.id))
            return [_to_entity(RawListingItem, r) for r in rows]

    # --- items, evidence, decisions ----------------------------------------------------------

    def list_items(self, f: ItemFilter) -> list[RegulatoryItem]:
        with self._tx() as session:
            stmt = select(m.RegulatoryItem).order_by(
                m.RegulatoryItem.first_seen_at, m.RegulatoryItem.id
            )
            if f.cycle is not None:
                run_ids = select(m.CrawlRun.id).where(
                    m.CrawlRun.cycle_date == f.cycle.cycle_date, m.CrawlRun.cycle == f.cycle.cycle
                )
                stmt = stmt.where(
                    m.RegulatoryItem.id.in_(
                        select(m.ItemObservation.item_id).where(
                            m.ItemObservation.run_id.in_(run_ids)
                        )
                    )
                )
            if f.state is not None:
                stmt = stmt.where(m.RegulatoryItem.state == f.state)
            if f.channel is not None:
                stmt = stmt.where(m.RegulatoryItem.channel == f.channel)
            if f.min_relevance is not None:
                stmt = stmt.where(m.RegulatoryItem.relevance_score >= f.min_relevance)
            items = [_to_entity(RegulatoryItem, r) for r in session.scalars(stmt)]
            if f.q:
                # Matched in Python so lower-casing and substring rules are identical on every
                # database (SQLite's LOWER only handles ASCII).
                needle = f.q.strip().lower()
                authorities = {
                    sid: authority
                    for sid, authority in session.execute(select(m.Source.id, m.Source.authority))
                }
                items = [
                    i
                    for i in items
                    if needle
                    in " ".join(
                        [
                            i.title,
                            i.ref_no or "",
                            i.internal_ref,
                            authorities.get(i.source_id, ""),
                            i.summary or "",
                        ]
                    ).lower()
                ]
            return items

    def get_item(self, item_id: str) -> RegulatoryItem | None:
        with self._tx() as session:
            row = session.get(m.RegulatoryItem, item_id)
            return None if row is None else _to_entity(RegulatoryItem, row)

    def _observations(self, where: Any) -> list[ItemObservation]:
        with self._tx() as session:
            stmt = (
                select(m.ItemObservation)
                .join(m.CrawlRun, m.ItemObservation.run_id == m.CrawlRun.id)
                .where(where)
                .order_by(m.CrawlRun.started_at, m.ItemObservation.id)
            )
            return [_to_entity(ItemObservation, r) for r in session.scalars(stmt)]

    def list_observations(self, item_id: str) -> list[ItemObservation]:
        return self._observations(m.ItemObservation.item_id == item_id)

    def list_observations_for_runs(self, run_ids: list[str]) -> list[ItemObservation]:
        return self._observations(m.ItemObservation.run_id.in_(run_ids))

    def list_documents(self, item_id: str) -> list[Document]:
        with self._tx() as session:
            stmt = (
                select(m.Document)
                .where(m.Document.item_id == item_id)
                .order_by(m.Document.fetched_at.is_(None), m.Document.fetched_at, m.Document.id)
            )
            return [_to_entity(Document, r) for r in session.scalars(stmt)]

    def get_document(self, document_id: str) -> Document | None:
        with self._tx() as session:
            row = session.get(m.Document, document_id)
            return None if row is None else _to_entity(Document, row)

    def add_document(
        self,
        doc: Document,
        content: bytes | None,
        new_state: ItemState | None,
        actor: str,
        text: str | None = None,
    ) -> Document:
        with self._tx() as session:
            session.add(_to_row(m.Document, doc))
            if content is not None and doc.sha256 and doc.path:
                self._write_once(doc.path, content)
            if text is not None and doc.text_path:
                self._write_once(doc.text_path, text.encode("utf-8"))
            self._audit(session, actor, "document.added", "document", doc.id, None, _dump(doc))
            if new_state is not None:
                self._set_state(session, doc.item_id, new_state, actor)
            return doc.model_copy(deep=True)

    def read_text(self, document_id: str) -> str | None:
        with self._tx() as session:
            row = session.get(m.Document, document_id)
            relative = None if row is None else row.text_path
        if not relative:
            return None
        path = self._file(relative)
        return path.read_text(encoding="utf-8") if path.is_file() else None

    def find_item_by_fingerprint(self, fingerprint: str) -> RegulatoryItem | None:
        with self._tx() as session:
            row = session.scalars(
                select(m.RegulatoryItem).where(m.RegulatoryItem.fingerprint == fingerprint)
            ).first()
            return None if row is None else _to_entity(RegulatoryItem, row)

    def save_detection(
        self,
        item: RegulatoryItem,
        observations: list[ItemObservation],
        is_new: bool,
        actor: str,
    ) -> RegulatoryItem:
        with self._tx() as session:
            if is_new:
                item = item.model_copy(
                    update={"internal_ref": next_internal_ref(session, item.first_seen_at.year)}
                )
                session.add(_to_row(m.RegulatoryItem, item))
                session.flush()  # the item row must exist before its observations
                before = None
            else:
                row = session.get(m.RegulatoryItem, item.id)
                if row is None:
                    raise KeyError(item.id)
                before = _dump(_to_entity(RegulatoryItem, row))
                for name, value in _row_values(m.RegulatoryItem, item).items():
                    if name not in ("id", "internal_ref", "fingerprint", "first_seen_run_id"):
                        setattr(row, name, value)
            for obs in observations:
                session.add(_to_row(m.ItemObservation, obs))
            action = "regulatory_item.detected" if is_new else "regulatory_item.updated"
            self._audit(session, actor, action, "regulatory_item", item.id, before, _dump(item))
            return item.model_copy(deep=True)

    def mark_items_seen(self, item_ids: list[str], seen_at: datetime, actor: str) -> None:
        if not item_ids:
            return
        with self._tx() as session:
            for row in session.scalars(
                select(m.RegulatoryItem).where(m.RegulatoryItem.id.in_(item_ids))
            ):
                row.last_seen_at = seen_at
            self._audit(
                session,
                actor,
                "regulatory_items.seen",
                "regulatory_item",
                item_ids[0],
                None,
                {"count": len(item_ids), "seen_at": seen_at.isoformat()},
            )

    def _set_state(self, session: Session, item_id: str, state: ItemState, actor: str) -> None:
        row = session.get(m.RegulatoryItem, item_id)
        if row is None:
            raise KeyError(item_id)
        if row.state == state:
            return
        before = {"state": row.state.value}
        row.state = state
        self._audit(
            session,
            actor,
            "regulatory_item.state_changed",
            "regulatory_item",
            item_id,
            before,
            {"state": state.value},
        )

    def list_decisions(self, item_id: str) -> list[ReviewDecision]:
        with self._tx() as session:
            stmt = (
                select(m.ReviewDecision)
                .where(m.ReviewDecision.item_id == item_id)
                .order_by(m.ReviewDecision.decided_at, m.ReviewDecision.id)
            )
            return [_to_entity(ReviewDecision, r) for r in session.scalars(stmt)]

    def save_decisions(
        self, decisions: list[ReviewDecision], new_state: ItemState, actor: str
    ) -> list[ReviewDecision]:
        with self._tx() as session:
            saved: list[ReviewDecision] = []
            for d in decisions:
                if session.get(m.RegulatoryItem, d.item_id) is None:
                    raise KeyError(d.item_id)
                session.merge(_to_row(m.ReviewDecision, d))
                if d.is_draft:
                    action = "review_decision.saved"
                elif d.dismiss_reason:
                    action = "review_decision.dismissed"
                else:
                    action = "review_decision.accepted"
                session.flush()
                self._audit(session, actor, action, "review_decision", d.id, None, _dump(d))
                self._set_state(session, d.item_id, new_state, actor)
                saved.append(d.model_copy(deep=True))
            return saved

    # --- daily update ------------------------------------------------------------------------

    def find_daily_update(self, ref: CycleRef) -> DailyUpdate | None:
        with self._tx() as session:
            row = session.scalars(
                select(m.DailyUpdate).where(
                    m.DailyUpdate.cycle_date == ref.cycle_date, m.DailyUpdate.cycle == ref.cycle
                )
            ).first()
            return None if row is None else _to_entity(DailyUpdate, row)

    def get_daily_update(self, update_id: str) -> DailyUpdate | None:
        with self._tx() as session:
            row = session.get(m.DailyUpdate, update_id)
            return None if row is None else _to_entity(DailyUpdate, row)

    def list_daily_update_items(self, update_id: str) -> list[DailyUpdateItem]:
        with self._tx() as session:
            stmt = (
                select(m.DailyUpdateItem)
                .where(m.DailyUpdateItem.update_id == update_id)
                .order_by(m.DailyUpdateItem.serial_no)
            )
            return [_to_entity(DailyUpdateItem, r) for r in session.scalars(stmt)]

    def list_daily_updates_for_item(self, item_id: str) -> list[DailyUpdate]:
        with self._tx() as session:
            stmt = (
                select(m.DailyUpdate)
                .where(
                    m.DailyUpdate.id.in_(
                        select(m.DailyUpdateItem.update_id).where(
                            m.DailyUpdateItem.item_id == item_id
                        )
                    )
                )
                .order_by(m.DailyUpdate.cycle_date, m.DailyUpdate.id)
            )
            return [_to_entity(DailyUpdate, r) for r in session.scalars(stmt)]

    def save_daily_update(
        self, update: DailyUpdate, rows: list[DailyUpdateItem], actor: str
    ) -> DailyUpdate:
        with self._tx() as session:
            existing_row = session.get(m.DailyUpdate, update.id)
            existing = None if existing_row is None else _to_entity(DailyUpdate, existing_row)
            if existing is not None and existing.status != UpdateStatus.DRAFT:
                raise ValueError("Only a draft can be changed")
            old_ids = [
                r.item_id
                for r in session.scalars(
                    select(m.DailyUpdateItem)
                    .where(m.DailyUpdateItem.update_id == update.id)
                    .order_by(m.DailyUpdateItem.serial_no)
                )
            ]
            before = None if existing is None else {"update": _dump(existing), "item_ids": old_ids}
            session.merge(_to_row(m.DailyUpdate, update))
            session.flush()
            # Replace the rows: remove first so renumbering never collides with the old serials.
            session.execute(
                delete(m.DailyUpdateItem).where(m.DailyUpdateItem.update_id == update.id)
            )
            session.flush()
            for r in rows:
                session.add(_to_row(m.DailyUpdateItem, r))
            action = "daily_update.drafted" if existing is None else "daily_update.changed"
            self._audit(
                session,
                actor,
                action,
                "daily_update",
                update.id,
                before,
                {"update": _dump(update), "item_ids": [r.item_id for r in rows]},
            )
            return update.model_copy(deep=True)

    def approve_daily_update(self, update_id: str, approver: str, actor: str) -> DailyUpdate:
        with self._tx() as session:
            row = session.get(m.DailyUpdate, update_id)
            if row is None:
                raise KeyError(update_id)
            if row.status != UpdateStatus.DRAFT:
                raise ValueError("Only a draft can be approved")
            before = _dump(_to_entity(DailyUpdate, row))
            row.status = UpdateStatus.APPROVED
            row.approved_by = approver
            row.approved_at = now_ist()
            session.flush()
            update = _to_entity(DailyUpdate, row)
            self._audit(
                session,
                actor,
                "daily_update.approved",
                "daily_update",
                update_id,
                before,
                _dump(update),
            )
            for item_id in session.scalars(
                select(m.DailyUpdateItem.item_id)
                .where(m.DailyUpdateItem.update_id == update_id)
                .order_by(m.DailyUpdateItem.serial_no)
            ):
                self._set_state(session, item_id, ItemState.PUBLISHED, actor)
            return update

    def mark_daily_update_exported(self, update_id: str, actor: str) -> DailyUpdate:
        with self._tx() as session:
            row = session.get(m.DailyUpdate, update_id)
            if row is None:
                raise KeyError(update_id)
            if row.status == UpdateStatus.DRAFT:
                raise ValueError("Approve the update before exporting")
            if row.status == UpdateStatus.EXPORTED:
                return _to_entity(DailyUpdate, row)
            before = _dump(_to_entity(DailyUpdate, row))
            row.status = UpdateStatus.EXPORTED
            row.exported_at = now_ist()
            session.flush()
            update = _to_entity(DailyUpdate, row)
            self._audit(
                session,
                actor,
                "daily_update.exported",
                "daily_update",
                update_id,
                before,
                _dump(update),
            )
            return update

    # --- configuration -----------------------------------------------------------------------

    def _setting_row(self, session: Session, key: str) -> m.Setting:
        row = session.get(m.Setting, key)
        if row is None:
            raise KeyError(key)
        return row

    def _setting(self, key: str) -> Setting:
        with self._tx() as session:
            return _to_entity(Setting, self._setting_row(session, key))

    # SME routing, recipients and the email wording are approved settings (same as the in-memory
    # repository); these three methods are read-only views of the approved values.
    def list_sme_functions(self) -> list[SmeFunction]:
        s = self._setting("sme-routing")
        return [SmeFunction(**item, version=s.version) for item in copy.deepcopy(s.value)]

    def list_recipients(self) -> list[Recipient]:
        s = self._setting("recipients")
        return [Recipient(**item, version=s.version) for item in copy.deepcopy(s.value)]

    def get_email_template(self) -> EmailTemplate:
        s = self._setting("email-template")
        return EmailTemplate(id="tpl-daily-update", version=s.version, **copy.deepcopy(s.value))

    def list_settings(self) -> list[Setting]:
        with self._tx() as session:
            rows = session.scalars(select(m.Setting).order_by(m.Setting.key))
            return [_to_entity(Setting, r) for r in rows]

    def get_setting(self, key: str) -> Setting | None:
        with self._tx() as session:
            row = session.get(m.Setting, key)
            return None if row is None else _to_entity(Setting, row)

    def _save_setting(self, session: Session, row: m.Setting, setting: Setting) -> None:
        for name, value in _row_values(m.Setting, setting).items():
            if name != "key":
                setattr(row, name, value)
        session.flush()

    def propose_setting(self, key: str, value: Any, requested_by: str, actor: str) -> Setting:
        with self._tx() as session:
            row = self._setting_row(session, key)
            setting = _to_entity(Setting, row)
            before = _dump(setting)
            setting.pending = SettingChange(
                value=value,
                requested_by=requested_by,
                requested_at=now_ist(),
                version=setting.version + 1,
            )
            self._save_setting(session, row, setting)
            self._audit(
                session, actor, "setting.change_requested", "setting", key, before, _dump(setting)
            )
            return setting

    def approve_setting(self, key: str, approver: str, actor: str) -> Setting:
        with self._tx() as session:
            row = self._setting_row(session, key)
            setting = _to_entity(Setting, row)
            if setting.pending is None:
                raise ValueError("Nothing is waiting for approval")
            before = _dump(setting)
            pending = setting.pending
            setting.value = pending.value
            setting.version = pending.version
            setting.updated_by = pending.requested_by
            setting.updated_at = now_ist()
            setting.pending = None
            self._save_setting(session, row, setting)
            self._audit(
                session,
                actor,
                "setting.change_approved",
                "setting",
                key,
                before,
                {**_dump(setting), "approved_by": approver},
            )
            return setting

    def reject_setting(self, key: str, reason: str, actor: str) -> Setting:
        with self._tx() as session:
            row = self._setting_row(session, key)
            setting = _to_entity(Setting, row)
            if setting.pending is None:
                raise ValueError("Nothing is waiting for approval")
            before = _dump(setting)
            setting.pending = None
            self._save_setting(session, row, setting)
            self._audit(
                session,
                actor,
                "setting.change_rejected",
                "setting",
                key,
                before,
                {**_dump(setting), "reason": reason},
            )
            return setting

    # --- audit -------------------------------------------------------------------------------

    def list_audit(
        self,
        entity: str | None = None,
        actor: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[AuditEvent]:
        with self._tx() as session:
            stmt = select(m.AuditEvent).order_by(m.AuditEvent.ts.desc(), m.AuditEvent.id.desc())
            if entity:
                stmt = stmt.where(
                    or_(m.AuditEvent.entity == entity, m.AuditEvent.entity_id == entity)
                )
            if actor:
                stmt = stmt.where(func.lower(m.AuditEvent.actor) == actor.lower())
            # Dates are IST days, as everywhere in the application.
            if date_from:
                start = datetime(date_from.year, date_from.month, date_from.day, tzinfo=IST)
                stmt = stmt.where(m.AuditEvent.ts >= start)
            if date_to:
                end = datetime(date_to.year, date_to.month, date_to.day, tzinfo=IST) + timedelta(
                    days=1
                )
                stmt = stmt.where(and_(m.AuditEvent.ts < end))
            return [_to_entity(AuditEvent, r) for r in session.scalars(stmt)]
