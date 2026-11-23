"""Health and the POC user (who am I, who can I switch to)."""

from fastapi import APIRouter, Depends

from app.api.deps import current_user, get_services
from app.schemas.entities import AppUser
from app.services import Services

router = APIRouter(tags=["session"])


@router.get("/health", summary="Liveness check")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/me", response_model=AppUser, summary="The current POC user")
def me(user: AppUser = Depends(current_user)) -> AppUser:
    return user


@router.get("/users", response_model=list[AppUser], summary="POC users for the switcher")
def users(services: Services = Depends(get_services)) -> list[AppUser]:
    return services.users.list_users()
