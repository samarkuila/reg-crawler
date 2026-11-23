"""Luna endpoints: status, flags, Ask Luna. Advisory; validation only here."""

from typing import Literal

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_user, get_services
from app.schemas.api import AgentFlagView, AgentStatusOut, ItemDetail, ResolveFlagIn
from app.schemas.entities import AppUser
from app.schemas.enums import FlagStatus
from app.services import Services

router = APIRouter(tags=["agent"])


@router.get("/agent/status", response_model=AgentStatusOut, summary="Is Luna on, and its budget")
def agent_status(services: Services = Depends(get_services)) -> AgentStatusOut:
    return services.luna.status()


@router.get("/agent/flags", response_model=list[AgentFlagView], summary="Luna flags")
def agent_flags(
    source_id: str | None = None,
    status: Literal["OPEN", "RESOLVED", "FALSE_ALARM", "ALL"] = Query("OPEN"),
    services: Services = Depends(get_services),
) -> list[AgentFlagView]:
    return services.luna.flags(source_id, None if status == "ALL" else FlagStatus(status))


@router.post(
    "/agent/flags/{flag_id}/resolve",
    response_model=AgentFlagView,
    summary="Mark a possible-miss or disagreement flag resolved or a false alarm",
)
def resolve_flag(
    flag_id: str,
    body: ResolveFlagIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> AgentFlagView:
    return services.luna.resolve(user, flag_id, FlagStatus(body.status))


@router.post(
    "/items/{item_id}/ask-luna",
    response_model=ItemDetail,
    summary="Ask Luna to screen and extract this item now",
)
def ask_luna(
    item_id: str,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> ItemDetail:
    return services.luna.ask(user, item_id)
