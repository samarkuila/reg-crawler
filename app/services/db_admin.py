"""Database admin: admin-only browse, edit, delete and reset (docs/constraints-and-policies.md).

This is a deliberate exception to the maker-checker rule for data (rule 9): an admin fixes rows
directly. It is still audited (rule 8) and still cannot touch write-once tables (rule 7).
"""

import os
from typing import Any

from app.repositories.db_admin import (
    DbAdminError,
    DbConflict,
    DbNotFound,
    SqlDbAdmin,
)
from app.schemas.api import DbColumn, DbTable, DbTablePage
from app.schemas.entities import AppUser
from app.services.errors import ConflictError, DomainError, InvalidError, NotFoundError
from app.services.permissions import DB_ADMIN_ROLES, require_role

RESET_WORD = "RESET"


def enabled() -> bool:
    """On unless APP_ENV=prod; DB_ADMIN_ENABLED=true/false overrides either way."""
    flag = os.environ.get("DB_ADMIN_ENABLED", "").strip().lower()
    if flag:
        return flag == "true"
    return os.environ.get("APP_ENV", "dev").strip().lower() != "prod"


def _translate(exc: DbAdminError) -> DomainError:
    if isinstance(exc, DbNotFound):
        return NotFoundError(str(exc))
    if isinstance(exc, DbConflict):
        return ConflictError(str(exc))
    return InvalidError(str(exc))


class DbAdminService:
    def __init__(self, engine: Any | None) -> None:
        self._backend = SqlDbAdmin(engine) if engine is not None else None

    def _ready(self, user: AppUser, what: str) -> SqlDbAdmin:
        require_role(user, DB_ADMIN_ROLES, what)
        if not enabled():
            raise NotFoundError("The Database page is switched off (DB_ADMIN_ENABLED).")
        if self._backend is None:
            raise ConflictError(
                "The Database page needs the SQL backend. Start the app with REPO_BACKEND=sql."
            )
        return self._backend

    def tables(self, user: AppUser) -> list[DbTable]:
        backend = self._ready(user, "open the database page")
        return [DbTable(**vars(t)) for t in backend.list_tables()]

    def page(
        self, user: AppUser, table: str, limit: int, offset: int, query: str | None
    ) -> DbTablePage:
        backend = self._ready(user, "open the database page")
        try:
            columns, rows, total, reason = backend.read(table, limit, offset, query)
        except DbAdminError as exc:
            raise _translate(exc) from exc
        return DbTablePage(
            table=table,
            columns=[DbColumn(**vars(c)) for c in columns],
            rows=rows,
            total=total,
            editable=reason is None,
            reason=reason,
        )

    def create(self, user: AppUser, table: str, values: dict[str, Any]) -> dict[str, Any]:
        backend = self._ready(user, "add database rows")
        try:
            return backend.insert_row(table, values, user.name)
        except DbAdminError as exc:
            raise _translate(exc) from exc

    def update(
        self, user: AppUser, table: str, key: dict[str, Any], changes: dict[str, Any]
    ) -> dict[str, Any]:
        backend = self._ready(user, "edit database rows")
        try:
            return backend.update_row(table, key, changes, user.name)
        except DbAdminError as exc:
            raise _translate(exc) from exc

    def delete(self, user: AppUser, table: str, key: dict[str, Any], cascade: bool = False) -> int:
        backend = self._ready(user, "delete database rows")
        try:
            return backend.delete_row(table, key, user.name, cascade)
        except DbAdminError as exc:
            raise _translate(exc) from exc

    def reset(self, user: AppUser, confirm: str) -> str:
        backend = self._ready(user, "reset the database")
        if confirm.strip() != RESET_WORD:
            raise InvalidError(f"Type {RESET_WORD} to confirm the reset.")
        try:
            return backend.reset(user.name)
        except DbAdminError as exc:
            raise _translate(exc) from exc
