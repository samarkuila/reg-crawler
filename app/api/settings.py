"""Versioned settings: a change is pending until a different approver approves it."""

from fastapi import APIRouter, Depends

from app.api.deps import current_user, get_services
from app.schemas.api import SettingIn, SettingRejectIn, SettingView, SmeFunctionOption
from app.schemas.entities import AppUser
from app.services import Services

router = APIRouter(tags=["settings"])


@router.get(
    "/sme-functions",
    response_model=list[SmeFunctionOption],
    summary="Active SME functions (for the review form)",
)
def list_sme_functions(services: Services = Depends(get_services)) -> list[SmeFunctionOption]:
    return services.config.list_sme_functions()


@router.get("/settings", response_model=list[SettingView], summary="All settings")
def list_settings(services: Services = Depends(get_services)) -> list[SettingView]:
    return services.config.list_settings()


@router.get("/settings/{key}", response_model=SettingView, summary="One setting")
def get_setting(key: str, services: Services = Depends(get_services)) -> SettingView:
    return services.config.get_setting(key)


@router.put(
    "/settings/{key}",
    response_model=SettingView,
    summary="Propose a new value (pending approval)",
)
def put_setting(
    key: str,
    body: SettingIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SettingView:
    return services.config.propose_setting(user, key, body.value)


@router.post(
    "/settings/{key}/approve",
    response_model=SettingView,
    summary="Approve a pending change (approver, not the requester)",
)
def approve_setting(
    key: str, user: AppUser = Depends(current_user), services: Services = Depends(get_services)
) -> SettingView:
    return services.config.approve_setting(user, key)


@router.post(
    "/settings/{key}/reject",
    response_model=SettingView,
    summary="Reject a pending change (approver, not the requester)",
)
def reject_setting(
    key: str,
    body: SettingRejectIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> SettingView:
    return services.config.reject_setting(user, key, body.reason)
