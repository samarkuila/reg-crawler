"""Admin Database page API: browse, edit and delete rows, reset. Admin role: see the service."""

from typing import Any

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_user, get_services
from app.schemas.api import (
    DbDeleteOut,
    DbResetIn,
    DbResetOut,
    DbRowCreateIn,
    DbRowDeleteIn,
    DbRowUpdateIn,
    DbTable,
    DbTablePage,
)
from app.schemas.entities import AppUser
from app.services import Services

router = APIRouter(prefix="/admin/db", tags=["database admin"])


@router.get("/tables", response_model=list[DbTable], summary="Every table with its row count")
def tables(
    user: AppUser = Depends(current_user), services: Services = Depends(get_services)
) -> list[DbTable]:
    return services.db_admin.tables(user)


@router.get("/tables/{table}", response_model=DbTablePage, summary="Rows of one table")
def rows(
    table: str,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    q: str | None = None,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> DbTablePage:
    return services.db_admin.page(user, table, limit, offset, q)


@router.post(
    "/tables/{table}/rows",
    status_code=201,
    summary="Add one row (audited)",
)
def create_row(
    table: str,
    body: DbRowCreateIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return services.db_admin.create(user, table, body.values)


@router.put("/tables/{table}/rows", summary="Edit one row (audited)")
def update_row(
    table: str,
    body: DbRowUpdateIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> dict[str, Any]:
    return services.db_admin.update(user, table, body.key, body.changes)


@router.post(
    "/tables/{table}/rows/delete",
    response_model=DbDeleteOut,
    summary="Delete one row, and with cascade the rows that point to it (audited)",
)
def delete_row(
    table: str,
    body: DbRowDeleteIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> DbDeleteOut:
    return DbDeleteOut(deleted=services.db_admin.delete(user, table, body.key, body.cascade))


@router.post("/reset", response_model=DbResetOut, summary="Back up, then rebuild an empty database")
def reset(
    body: DbResetIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> DbResetOut:
    return DbResetOut(backup=services.db_admin.reset(user, body.confirm))
