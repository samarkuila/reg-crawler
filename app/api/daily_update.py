"""Daily update: preview, reorder, approve, export. The system never sends email."""

from fastapi import APIRouter, Depends, Query, Response

from app.api.deps import current_user, get_services
from app.schemas.api import DailyUpdatePreview, ReorderIn
from app.schemas.entities import AppUser
from app.services import Services

router = APIRouter(tags=["daily-update"])


@router.get(
    "/daily-update/preview",
    response_model=DailyUpdatePreview,
    summary="Rendered draft for a cycle (built only from reviewed items)",
)
def preview(
    cycle: str | None = Query(None, description="Cycle id, e.g. 2026-11-20-EOD (default: latest)"),
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> DailyUpdatePreview:
    return services.daily_update.preview(user, cycle)


@router.get(
    "/daily-update/cycle-export.csv",
    summary="Every captured item of the cycle (incl. low relevance and dismissed) for Excel",
)
def export_cycle(
    cycle: str | None = Query(None, description="Cycle id, e.g. 2026-11-20-EOD (default: latest)"),
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> Response:
    content, filename = services.cycle_export.export_csv(user, cycle)
    return Response(
        content=content,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post(
    "/daily-update/{update_id}/reorder", response_model=DailyUpdatePreview, summary="Set S.No order"
)
def reorder(
    update_id: str,
    body: ReorderIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> DailyUpdatePreview:
    return services.daily_update.reorder(user, update_id, body.item_ids)


@router.post(
    "/daily-update/{update_id}/approve",
    response_model=DailyUpdatePreview,
    summary="Approve the draft (approver, not the preparer)",
)
def approve(
    update_id: str,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> DailyUpdatePreview:
    return services.daily_update.approve(user, update_id)


def _download(update_id: str, kind: str, user: AppUser, services: Services) -> Response:
    export = services.daily_update.export(user, update_id, kind)
    return Response(
        content=export.content,
        media_type=export.media_type,
        headers={"Content-Disposition": f'attachment; filename="{export.filename}"'},
    )


@router.get("/daily-update/{update_id}/export.eml", summary="Download the unsent .eml draft")
def export_eml(
    update_id: str,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> Response:
    return _download(update_id, "eml", user, services)


@router.get("/daily-update/{update_id}/export.html", summary="Download the draft as HTML")
def export_html(
    update_id: str,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> Response:
    return _download(update_id, "html", user, services)
