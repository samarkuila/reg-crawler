"""Shared API dependencies: the service container and the POC user (from the switcher cookie)."""

from fastapi import Depends, Request

from app.repositories.factory import make_repository
from app.schemas.entities import AppUser
from app.services import Services, build_services

USER_COOKIE = "poc_user"

_services: Services | None = None


def get_services() -> Services:
    global _services
    if _services is None:
        _services = build_services(make_repository())
    return _services


def reset_services() -> None:
    """Rebuild repository and services from seed data (used by tests and `make seed-demo`)."""
    global _services
    _services = None


def current_user(request: Request, services: Services = Depends(get_services)) -> AppUser:
    """POC only: the selected user is a cookie. V2 replaces this with Entra ID SSO."""
    return services.users.resolve(request.cookies.get(USER_COOKIE))
