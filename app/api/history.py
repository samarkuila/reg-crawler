"""Source health extras and item history. Validation only; logic is in services."""

from datetime import date

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import get_services
from app.schemas.api import (
    CheckLog,
    CompareResult,
    HistoryResults,
    SourceGrid,
    Trail,
)
from app.services import Services
from app.services.stored_copy import StoredFile

router = APIRouter(tags=["health-history"])


def _file(stored: StoredFile) -> Response:
    return Response(
        content=stored.content,
        media_type=stored.media_type,
        headers={
            "Content-Disposition": f'inline; filename="{stored.filename}"',
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


@router.get("/source-health/grid", response_model=SourceGrid, summary="Last four cycles per source")
def grid(services: Services = Depends(get_services)) -> SourceGrid:
    return services.health.grid()


@router.get(
    "/source-health/compare",
    response_model=CompareResult,
    summary="Items seen on RSS only, HTML only or both",
)
def compare(
    source_id: str,
    cycle: str | None = Query(None, description="Cycle id, e.g. 2026-11-20-EOD (default: latest)"),
    services: Services = Depends(get_services),
) -> CompareResult:
    return services.health.compare(source_id, cycle)


@router.get("/source-health/checks/{check_id}/log", response_model=CheckLog, summary="Check log")
def check_log(check_id: str, services: Services = Depends(get_services)) -> CheckLog:
    return services.health.check_log(check_id)


@router.get("/snapshots/{snapshot_id}/content", summary="Stored copy of a listing snapshot")
def snapshot_content(snapshot_id: str, services: Services = Depends(get_services)) -> Response:
    return _file(services.health.snapshot_file(snapshot_id))


@router.get("/documents/{document_id}/content", summary="Stored copy of a document")
def document_content(document_id: str, services: Services = Depends(get_services)) -> Response:
    return _file(services.history.document_file(document_id))


@router.get("/history/search", response_model=HistoryResults, summary="Search every item")
def search(
    q: str | None = Query(None, max_length=200, description="Title, reference no. or internal ref"),
    authority: str | None = None,
    date_from: date | None = Query(None, alias="from", description="YYYY-MM-DD"),
    date_to: date | None = Query(None, alias="to", description="YYYY-MM-DD"),
    services: Services = Depends(get_services),
) -> HistoryResults:
    return services.history.search(q, authority, date_from, date_to)


@router.get("/history/{item_id}/trail", response_model=Trail, summary="Six-step evidence chain")
def trail(item_id: str, services: Services = Depends(get_services)) -> Trail:
    return services.history.trail(item_id)


@router.get("/history/{item_id}/trail.csv", summary="Evidence chain as CSV")
def trail_csv(item_id: str, services: Services = Depends(get_services)) -> Response:
    content, filename = services.history.trail_csv(item_id)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
