"""Audit log (read only) and CSV export."""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import get_services
from app.schemas.api import AuditPage
from app.schemas.entities import AuditEvent
from app.services import Services

router = APIRouter(tags=["audit"])


@router.get("/audit", response_model=list[AuditEvent], summary="Audit events, newest first")
def list_audit(
    entity: str | None = Query(None, description="Entity name or entity id"),
    actor: str | None = None,
    from_: date | None = Query(None, alias="from", description="YYYY-MM-DD"),
    to: date | None = Query(None, description="YYYY-MM-DD"),
    action: str | None = None,
    services: Services = Depends(get_services),
) -> list[AuditEvent]:
    return services.audit.list_events(entity, actor, from_, to, action)


@router.get("/audit/view", response_model=AuditPage, summary="Audit events as readable rows")
def view_audit(
    entity: str | None = Query(None, description="Entity name or entity id"),
    actor: str | None = None,
    action: str | None = None,
    from_: date | None = Query(None, alias="from", description="YYYY-MM-DD"),
    to: date | None = Query(None, description="YYYY-MM-DD"),
    services: Services = Depends(get_services),
) -> AuditPage:
    return services.audit.view(entity, actor, from_, to, action)


@router.get("/audit/export.csv", summary="Audit events as CSV")
def export_audit(
    entity: str | None = None,
    actor: str | None = None,
    from_: date | None = Query(None, alias="from"),
    to: date | None = None,
    action: str | None = None,
    services: Services = Depends(get_services),
) -> Response:
    return Response(
        content=services.audit.export_csv(entity, actor, from_, to, action),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="audit-log.csv"'},
    )
