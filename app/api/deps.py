"""Shared API dependencies: the service container and the POC user (from the switcher cookie)."""

import os

from fastapi import Depends, Request

from app.repositories.factory import make_repository
from app.schemas.entities import AppUser
from app.services import Services, build_services
from app.services.source_test import MockSourceTester, SourceTester, SubprocessSourceTester

USER_COOKIE = "poc_user"

_services: Services | None = None


def make_source_tester() -> SourceTester:
    """SOURCE_TESTER=live runs the real spider (dry run); mock gives demo rows.

    Default: live with the SQL backend (real data), mock with the in-memory demo backend.
    """
    default = "live" if os.environ.get("REPO_BACKEND", "memory").lower() == "sql" else "mock"
    if os.environ.get("SOURCE_TESTER", default).strip().lower() == "live":
        return SubprocessSourceTester()
    return MockSourceTester()


def get_services() -> Services:
    global _services
    if _services is None:
        _services = build_services(make_repository(), make_source_tester())
    return _services


def reset_services() -> None:
    """Rebuild repository and services from seed data (used by tests and `make seed-demo`)."""
    global _services
    _services = None


def current_user(request: Request, services: Services = Depends(get_services)) -> AppUser:
    """POC only: the selected user is a cookie. V2 replaces this with Entra ID SSO."""
    return services.users.resolve(request.cookies.get(USER_COOKIE))
