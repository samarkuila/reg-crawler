"""Source register, versions and approvals."""

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile

from app.api.deps import current_user, get_services
from app.schemas.api import (
    BulkDecisionIn,
    BulkDecisionResult,
    BulkErrorsIn,
    BulkPreview,
    BulkSaveIn,
    BulkSaveResult,
    SourceChanges,
    SourceCreate,
    SourceTestResult,
    SourceView,
    VersionRejectIn,
)
from app.schemas.entities import AppUser, SourceVersion
from app.services import Services
from app.services.source_bulk import MAX_BYTES, template_csv

router = APIRouter(tags=["sources"])


@router.get("/sources", response_model=list[SourceView], summary="Source register")
def list_sources(
    user: AppUser = Depends(current_user), services: Services = Depends(get_services)
) -> list[SourceView]:
    return services.config.list_sources(user)


@router.post(
    "/sources",
    response_model=SourceView,
    status_code=201,
    summary="Add a source (starts as version 1, pending approval)",
)
def create_source(
    body: SourceCreate,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SourceView:
    return services.config.create_source(user, body)


# --- bulk add (fixed paths come first, so they are never read as a source id) ----------------


@router.get("/sources/bulk-import/template.csv", summary="CSV template for bulk add")
def bulk_template() -> Response:
    return Response(
        template_csv(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="sources-template.csv"'},
    )


@router.post(
    "/sources/bulk-import/preview",
    response_model=BulkPreview,
    summary="Check every row of an uploaded or pasted list. Nothing is saved.",
)
async def bulk_preview(
    file: UploadFile | None = File(None),
    text: str | None = Form(None),
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> BulkPreview:
    data = await file.read(MAX_BYTES + 1) if file is not None else None
    return services.source_bulk.preview(user, file.filename if file else None, data, text)


@router.post(
    "/sources/bulk-import/save",
    response_model=BulkSaveResult,
    status_code=201,
    summary="Save the valid rows as pending source versions (re-checked on the server)",
)
def bulk_save(
    body: BulkSaveIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> BulkSaveResult:
    return services.source_bulk.save(user, body.file_name, body.rows)


@router.post(
    "/sources/bulk-import/errors.csv",
    summary="errors.csv: the rows that failed, with the reason column",
)
def bulk_errors(
    body: BulkErrorsIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> Response:
    return Response(
        services.source_bulk.errors_csv(user, body.rows),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="errors.csv"'},
    )


@router.post(
    "/sources/bulk-decision/approve",
    response_model=list[BulkDecisionResult],
    summary="Approve the chosen pending versions one by one (one audit event each)",
)
def bulk_approve(
    body: BulkDecisionIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> list[BulkDecisionResult]:
    return services.config.approve_many(user, body.items)


@router.post(
    "/sources/bulk-decision/reject",
    response_model=list[BulkDecisionResult],
    summary="Reject the chosen pending versions one by one (one audit event each)",
)
def bulk_reject(
    body: BulkDecisionIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> list[BulkDecisionResult]:
    return services.config.reject_many(user, body.items, body.reason)


@router.post(
    "/sources/{source_id}/test",
    response_model=SourceTestResult,
    summary="Dry-run one source and return parsed rows (nothing is saved)",
)
def test_source(
    source_id: str,
    polite: bool = False,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SourceTestResult:
    return services.config.test_source(user, source_id, polite=polite)


@router.post(
    "/sources/{source_id}/rebaseline",
    response_model=SourceView,
    summary="Apply the first-run window again on the next crawl (approver; deletes nothing)",
)
def rebaseline(
    source_id: str,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SourceView:
    return services.config.rebaseline(user, source_id)


@router.delete(
    "/sources/{source_id}",
    response_model=SourceVersion,
    status_code=202,
    summary="Propose deleting a source (pending approval; its history is kept)",
)
def delete_source(
    source_id: str,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SourceVersion:
    return services.config.delete_source(user, source_id)


@router.get(
    "/sources/{source_id}/versions",
    response_model=list[SourceVersion],
    summary="Version history of a source",
)
def list_versions(
    source_id: str, services: Services = Depends(get_services)
) -> list[SourceVersion]:
    return services.config.list_versions(source_id)


@router.post(
    "/sources/{source_id}/versions",
    response_model=SourceVersion,
    status_code=201,
    summary="Propose a change (creates a pending version)",
)
def propose_version(
    source_id: str,
    body: SourceChanges,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SourceVersion:
    return services.config.propose_version(user, source_id, body)


@router.post(
    "/sources/{source_id}/versions/{version}/approve",
    response_model=SourceVersion,
    summary="Approve a pending version (approver, not the requester)",
)
def approve_version(
    source_id: str,
    version: int,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SourceVersion:
    return services.config.approve_version(user, source_id, version)


@router.post(
    "/sources/{source_id}/versions/{version}/reject",
    response_model=SourceVersion,
    summary="Reject a pending version (approver, not the requester)",
)
def reject_version(
    source_id: str,
    version: int,
    body: VersionRejectIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SourceVersion:
    return services.config.reject_version(user, source_id, version, body.reason)
