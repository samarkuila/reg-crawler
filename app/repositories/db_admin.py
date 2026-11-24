"""Database admin on a SQL engine: browse, edit and delete rows of any table, or reset it.

Used only by the admin Database page (docs/constraints-and-policies.md, "Database admin").
Rules that still hold here:
- tables with an append-only trigger (snapshot, document, audit_event, ai_extraction) are browse
  only; the database refuses to change them and so does this class (rule 7);
- every edit and delete writes an audit event in the same transaction (rule 8);
- a reset moves the old database and stored files to data/backups/<timestamp> first.
"""

import base64
import json
import os
import shutil
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Engine,
    Enum,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    and_,
    cast,
    func,
    or_,
    select,
    text,
)
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db import models as m
from app.repositories.sql import new_id
from app.schemas.entities import now_ist

ROOT = Path(__file__).resolve().parents[2]
# Always protected, even if a trigger cannot be read (the migrations create these triggers).
APPEND_ONLY = frozenset({"snapshot", "document", "audit_event", "ai_extraction"})
READ_ONLY_OTHER = {"alembic_version": "This table is managed by the migrations."}
MAX_PAGE = 200


class DbAdminError(Exception):
    """Base for errors the service turns into domain errors."""

    kind = "invalid"


class DbNotFound(DbAdminError):
    kind = "not_found"


class DbConflict(DbAdminError):
    kind = "conflict"


@dataclass
class ColumnInfo:
    name: str
    type: str
    nullable: bool
    primary_key: bool
    choices: list[str] | None = None  # the allowed values of a fixed-choice column


@dataclass
class TableInfo:
    name: str
    rows: int
    editable: bool
    reason: str | None


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, bytes):
        return "base64:" + base64.b64encode(value).decode()
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    return value


class SqlDbAdmin:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    # --- reading -----------------------------------------------------------------------------

    def _meta(self) -> MetaData:
        meta = MetaData()
        meta.reflect(bind=self._engine)
        return meta

    def _table(self, name: str) -> Table:
        meta = self._meta()
        if name not in meta.tables:
            raise DbNotFound(f"There is no table called {name}.")
        return meta.tables[name]

    def _append_only_tables(self) -> set[str]:
        found = set(APPEND_ONLY)
        dialect = self._engine.dialect.name
        with self._engine.connect() as conn:
            if dialect == "sqlite":
                rows = conn.execute(
                    text(
                        "SELECT tbl_name FROM sqlite_master WHERE type='trigger' "
                        "AND name LIKE 'trg_%_no_update'"
                    )
                )
                found |= {r[0] for r in rows}
            elif dialect == "postgresql":
                rows = conn.execute(
                    text(
                        "SELECT c.relname FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid "
                        "WHERE NOT t.tgisinternal AND t.tgname LIKE 'trg_%_no_update'"
                    )
                )
                found |= {r[0] for r in rows}
        return found

    def _lock_reason(self, table: Table, append_only: set[str]) -> str | None:
        if table.name in READ_ONLY_OTHER:
            return READ_ONLY_OTHER[table.name]
        if table.name in append_only:
            return "Write-once table: the database refuses changes. Only a reset clears it."
        if not table.primary_key.columns:
            return "This table has no primary key, so a single row cannot be addressed."
        return None

    def list_tables(self) -> list[TableInfo]:
        meta = self._meta()
        append_only = self._append_only_tables()
        out: list[TableInfo] = []
        with self._engine.connect() as conn:
            for name in sorted(meta.tables):
                table = meta.tables[name]
                count = conn.execute(select(func.count()).select_from(table)).scalar_one()
                reason = self._lock_reason(table, append_only)
                out.append(TableInfo(name, int(count), reason is None, reason))
        return out

    def columns(self, table: Table) -> list[ColumnInfo]:
        known = m.Base.metadata.tables.get(table.name)
        out = []
        for c in table.columns:
            kind = known.c[c.name].type if known is not None and c.name in known.c else None
            choices = list(kind.enums) if isinstance(kind, Enum) else None
            out.append(
                ColumnInfo(c.name, str(c.type), bool(c.nullable), bool(c.primary_key), choices)
            )
        return out

    def read(
        self, name: str, limit: int, offset: int, query: str | None
    ) -> tuple[list[ColumnInfo], list[dict[str, Any]], int, str | None]:
        table = self._table(name)
        limit = max(1, min(limit, MAX_PAGE))
        stmt = select(table)
        if query:
            like = f"%{query.strip()}%"
            stmt = stmt.where(or_(*[cast(c, String).ilike(like) for c in table.columns]))
        pk = list(table.primary_key.columns) or list(table.columns)[:1]
        with self._engine.connect() as conn:
            total = conn.execute(select(func.count()).select_from(stmt.subquery())).scalar_one()
            rows = conn.execute(stmt.order_by(*pk).limit(limit).offset(max(0, offset))).mappings()
            data = [{k: _jsonable(v) for k, v in row.items()} for row in rows]
        reason = self._lock_reason(table, self._append_only_tables())
        return self.columns(table), data, int(total), reason

    # --- writing -----------------------------------------------------------------------------

    def _writable(self, name: str) -> Table:
        table = self._table(name)
        reason = self._lock_reason(table, self._append_only_tables())
        if reason:
            raise DbConflict(f"{name} cannot be changed here. {reason}")
        return table

    def _where(self, table: Table, key: dict[str, Any]) -> Any:
        pks = [c.name for c in table.primary_key.columns]
        if sorted(key) != sorted(pks):
            raise DbAdminError(f"The row key must be exactly: {', '.join(pks)}.")
        return and_(*[table.c[k] == self._coerce(table.c[k], v) for k, v in key.items()])

    @staticmethod
    def _coerce(column: Any, value: Any) -> Any:
        """Turn a JSON value into what the column stores. None stays None."""
        if value is None:
            return None
        kind = column.type
        try:
            if isinstance(kind, Boolean):
                if isinstance(value, bool):
                    return value
                if str(value).strip().lower() in ("true", "1", "yes"):
                    return True
                if str(value).strip().lower() in ("false", "0", "no"):
                    return False
                raise ValueError
            if isinstance(kind, JSON) and isinstance(value, str):
                return json.loads(value)
            if isinstance(kind, Integer):
                return int(value)
            if isinstance(kind, Numeric):
                return Decimal(str(value))
            if isinstance(kind, DateTime):
                return datetime.fromisoformat(str(value))
            if isinstance(kind, Date):
                return date.fromisoformat(str(value))
        except (ValueError, ArithmeticError) as exc:  # json errors are ValueErrors too
            raise DbAdminError(f"{column.name}: {value!r} is not a valid {kind}.") from exc
        return value

    def _row(self, session: Session, table: Table, where: Any) -> dict[str, Any]:
        row = session.execute(select(table).where(where)).mappings().first()
        if row is None:
            raise DbNotFound("That row no longer exists. Refresh the table.")
        return {k: _jsonable(v) for k, v in row.items()}

    def _audit(
        self,
        session: Session,
        actor: str,
        action: str,
        name: str,
        key: Any,
        before: Any,
        after: Any,
    ) -> None:
        session.add(
            m.AuditEvent(
                id=new_id("aud"),
                ts=now_ist(),
                actor=actor,
                action=action,
                entity=name,
                entity_id=json.dumps(key, sort_keys=True, default=str),
                before=before,
                after=after,
            )
        )

    def insert_row(self, name: str, values: dict[str, Any], actor: str) -> dict[str, Any]:
        """Add one row. Every key column must be given; the database checks the rest."""
        table = self._writable(name)
        pks = [c.name for c in table.primary_key.columns]
        for col in values:
            if col not in table.c:
                raise DbAdminError(f"{name} has no column called {col}.")
        clean = {col: self._coerce(table.c[col], v) for col, v in values.items() if v != ""}
        missing = [k for k in pks if clean.get(k) in (None, "")]
        if missing:
            raise DbAdminError(f"Fill in the key column(s): {', '.join(missing)}.")
        key = {k: _jsonable(clean[k]) for k in pks}
        try:
            with Session(self._engine) as session, session.begin():
                session.execute(table.insert().values(**clean))
                after = self._row(session, table, self._where(table, key))
                self._audit(session, actor, "admin.row_created", name, key, None, after)
        except (IntegrityError, DBAPIError) as exc:
            raise DbConflict(f"The database refused this row: {exc.orig}") from exc
        return after

    def update_row(
        self, name: str, key: dict[str, Any], changes: dict[str, Any], actor: str
    ) -> dict[str, Any]:
        table = self._writable(name)
        if not changes:
            raise DbAdminError("Nothing to change.")
        pks = {c.name for c in table.primary_key.columns}
        values: dict[str, Any] = {}
        for col, value in changes.items():
            if col not in table.c:
                raise DbAdminError(f"{name} has no column called {col}.")
            if col in pks:
                raise DbAdminError(f"{col} is part of the row's key and cannot be changed.")
            values[col] = self._coerce(table.c[col], value)
        where = self._where(table, key)
        try:
            with Session(self._engine) as session, session.begin():
                before = self._row(session, table, where)
                session.execute(table.update().where(where).values(**values))
                after = self._row(session, table, where)
                self._audit(session, actor, "admin.row_updated", name, key, before, after)
        except (IntegrityError, DBAPIError) as exc:
            raise DbConflict(f"The database refused this change: {exc.orig}") from exc
        return after

    def delete_row(self, name: str, key: dict[str, Any], actor: str, cascade: bool = False) -> int:
        """Delete one row. With `cascade`, rows that point at it go first (all audited).

        Returns how many rows were deleted in total. A write-once table that points at the row
        always blocks the delete.
        """
        table = self._writable(name)
        where = self._where(table, key)
        try:
            with Session(self._engine) as session, session.begin():
                before = self._row(session, table, where)
                gone = 1
                if cascade:
                    raw = session.execute(select(table).where(where)).mappings().one()
                    seen: set[Any] = {(name, json.dumps(key, sort_keys=True, default=str))}
                    gone += self._delete_dependents(
                        session, table, dict(raw), actor, self._meta(),
                        self._append_only_tables(), seen,
                    )  # fmt: skip
                session.execute(table.delete().where(where))
                self._audit(session, actor, "admin.row_deleted", name, key, before, None)
            return gone
        except (IntegrityError, DBAPIError) as exc:
            blockers = self._blockers(table, where)
            if blockers:
                raise DbConflict(
                    f"This row cannot be deleted because other rows still point to it: "
                    f"{blockers}. Delete or change those first, or tick "
                    "'Also delete the rows that point to it'."
                ) from exc
            raise DbConflict(f"The database refused this delete: {exc.orig}") from exc

    def _delete_dependents(
        self,
        session: Session,
        table: Table,
        raw: dict[str, Any],
        actor: str,
        meta: MetaData,
        locked: set[str],
        seen: set[Any],
    ) -> int:
        """Delete (and audit) every row that points at `raw`, deepest first. Returns the count."""
        count = 0
        for other in meta.tables.values():
            for fk in other.foreign_key_constraints:
                if fk.referred_table.name != table.name:
                    continue
                match = and_(*[other.c[e.parent.name] == raw[e.column.name] for e in fk.elements])
                rows = [dict(r) for r in session.execute(select(other).where(match)).mappings()]
                if not rows:
                    continue
                reason = self._lock_reason(other, locked)
                if reason:
                    raise DbConflict(
                        f"{len(rows)} row(s) in {other.name} point to this row, and "
                        f"{other.name} cannot be changed here. {reason}"
                    )
                for child in rows:
                    pk = {c.name: child[c.name] for c in other.primary_key.columns}
                    token = (other.name, json.dumps(_jsonable(pk), sort_keys=True))
                    if token in seen:
                        continue
                    seen.add(token)
                    count += self._delete_dependents(
                        session, other, child, actor, meta, locked, seen
                    )
                    session.execute(
                        other.delete().where(and_(*[other.c[k] == v for k, v in pk.items()]))
                    )
                    self._audit(
                        session,
                        actor,
                        "admin.row_deleted",
                        other.name,
                        _jsonable(pk),
                        {k: _jsonable(v) for k, v in child.items()},
                        None,
                    )
                    count += 1
        return count

    def _blockers(self, table: Table, where: Any) -> str:
        """Which tables hold rows that point at this row, e.g. "3 in source_version"."""
        meta = self._meta()
        found: list[str] = []
        try:
            with self._engine.connect() as conn:
                row = conn.execute(select(table).where(where)).mappings().first()
                if row is None:
                    return ""
                for other in meta.tables.values():
                    for fk in other.foreign_key_constraints:
                        if fk.referred_table.name != table.name:
                            continue
                        match = [other.c[e.parent.name] == row[e.column.name] for e in fk.elements]
                        count = conn.execute(
                            select(func.count()).select_from(other).where(and_(*match))
                        ).scalar_one()
                        if count:
                            found.append(f"{count} in {other.name}")
        except DBAPIError:
            return ""
        return ", ".join(found)

    # --- reset -------------------------------------------------------------------------------

    def reset(self, actor: str) -> str:
        """Back up, then rebuild an empty database (users and default settings only)."""
        url = make_url(str(self._engine.url))
        if url.get_backend_name() != "sqlite" or not url.database:
            raise DbConflict("Reset works only with a SQLite file database.")
        db_file = Path(url.database).resolve()
        data_dir = db_file.parent
        stamp = now_ist().strftime("%Y%m%d_%H%M%S")
        backup = data_dir / "backups" / stamp
        self._engine.dispose()  # release the file so it can be moved (Windows locks it)
        backup.mkdir(parents=True, exist_ok=True)
        try:
            for suffix in ("", "-wal", "-shm"):
                part = Path(str(db_file) + suffix)
                if part.exists():
                    shutil.move(str(part), backup / part.name)
            for folder in ("snapshots", "documents"):
                if (data_dir / folder).is_dir():
                    shutil.move(str(data_dir / folder), backup / folder)
        except OSError as exc:
            raise DbConflict(
                "Could not move the old database away (is another program using it?). "
                f"Nothing was deleted. {exc}"
            ) from exc
        self._rebuild(str(self._engine.url.render_as_string(hide_password=False)))
        with Session(self._engine) as session, session.begin():
            self._audit(
                session,
                actor,
                "admin.db_reset",
                "database",
                "all",
                None,
                {"backup": str(backup)},
            )
        return str(backup)

    def _rebuild(self, url: str) -> None:
        from alembic import command
        from alembic.config import Config

        from app.repositories import seed
        from app.repositories.sql import SqlRepository

        cfg = Config(str(ROOT / "alembic.ini"))
        cfg.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
        command.upgrade(cfg, "head")
        repo = SqlRepository(self._engine, files_root=os.environ.get("FILES_ROOT") or ".")
        repo.load(seed.USERS)  # someone must be able to sign in
        repo.load(seed.settings())  # default run times and wording
