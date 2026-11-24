"""Item endpoints: review queue, detail, decisions, bulk dismiss, manual upload."""

from fastapi import APIRouter, Depends, File, Query, UploadFile

from app.api.deps import current_user, get_services
from app.schemas.api import (
    BulkDismissIn,
    BulkDismissOut,
    DecisionIn,
    DecisionOut,
    ItemDetail,
    ItemListResponse,
)
from app.schemas.entities import AppUser, Document
from app.schemas.enums import Channel, ItemOrigin, ItemState
from app.services import Services
from app.services.review import MAX_UPLOAD_BYTES

router = APIRouter(tags=["items"])


@router.get("/items", response_model=ItemListResponse, summary="Review queue and search")
def list_items(
    cycle: str | None = Query(None, description="Cycle id, e.g. 2026-11-20-EOD (default: latest)"),
    state: ItemState | None = None,
    channel: Channel | None = None,
    q: str | None = Query(None, max_length=200, description="Search title, reference, authority"),
    min_relevance: int | None = Query(None, ge=0, le=100),
    origin: ItemOrigin | None = Query(
        None,
        description="BASELINE = stored by a first crawl, CRAWL = found later. "
        "Without it, baseline items are left out of the queue (see settings.yaml first_run).",
    ),
    services: Services = Depends(get_services),
) -> ItemListResponse:
    return services.review.list_items(cycle, state, channel, q, min_relevance, origin)


@router.post(
    "/items/bulk-dismiss",
    response_model=BulkDismissOut,
    summary="Dismiss many items with one reason",
)
def bulk_dismiss(
    body: BulkDismissIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> BulkDismissOut:
    return services.review.bulk_dismiss(user, body)


@router.get("/items/{item_id}", response_model=ItemDetail, summary="Item with traceability chain")
def get_item(item_id: str, services: Services = Depends(get_services)) -> ItemDetail:
    return services.review.item_detail(item_id)


@router.post(
    "/items/{item_id}/decision",
    response_model=DecisionOut,
    status_code=201,
    summary="Accept, save or dismiss an item",
)
def decide(
    item_id: str,
    body: DecisionIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> DecisionOut:
    return services.review.decide(user, item_id, body)


@router.post(
    "/items/{item_id}/upload",
    response_model=Document,
    status_code=201,
    summary="Upload a document for an item that needs a manual fetch",
)
async def upload_document(
    item_id: str,
    file: UploadFile = File(...),
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> Document:
    data = await file.read(MAX_UPLOAD_BYTES + 1)  # one byte over the limit is enough to refuse
    return services.review.upload_document(
        user, item_id, file.filename or "upload", file.content_type, data
    )
