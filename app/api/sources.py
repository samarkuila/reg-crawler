"""Source register, versions and approvals."""

from fastapi import APIRouter, Depends

from app.api.deps import current_user, get_services
from app.schemas.api import (
    SourceChanges,
    SourceCreate,
    SourceTestResult,
    SourceView,
    VersionRejectIn,
)
from app.schemas.entities import AppUser, SourceVersion
from app.services import Services

router = APIRouter(tags=["sources"])


@router.get("/sources", response_model=list[SourceView], summary="Source register")
def list_sources(services: Services = Depends(get_services)) -> list[SourceView]:
    return services.config.list_sources()


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


@router.post(
    "/sources/{source_id}/test",
    response_model=SourceTestResult,
    summary="Dry-run one source and return parsed rows (nothing is saved)",
)
def test_source(
    source_id: str,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SourceTestResult:
    return services.config.test_source(user, source_id)


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
