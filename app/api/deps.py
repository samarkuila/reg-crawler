"""Shared API dependencies: the service container and the POC user (from the switcher cookie)."""

import os

from fastapi import Depends, Request

from app.repositories.factory import make_repository
from app.schemas.entities import AppUser
from app.services import Services, build_services
from app.services.crawl_runner import CrawlRunner, SubprocessLauncher
from app.services.documents import DocumentService, SubprocessDocumentFetcher
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


def make_crawl_runner(repo) -> CrawlRunner:  # type: ignore[no-untyped-def]
    """CRAWL_RUNNER=live runs the real crawler; demo refuses (the demo data cannot be crawled).

    Default: live with the SQL backend, demo with the in-memory demo backend.
    """
    default = "live" if os.environ.get("REPO_BACKEND", "memory").lower() == "sql" else "demo"
    if os.environ.get("CRAWL_RUNNER", default).strip().lower() == "live":
        return CrawlRunner(
            repo, SubprocessLauncher(), DocumentService(repo, SubprocessDocumentFetcher())
        )
    return CrawlRunner(
        repo,
        SubprocessLauncher(),
        disabled_reason=(
            "Run now needs real data: start the app with REPO_BACKEND=sql "
            "(the demo data cannot be crawled)."
        ),
    )


def get_services() -> Services:
    global _services
    if _services is None:
        repo = make_repository()
        _services = build_services(repo, make_source_tester(), make_crawl_runner(repo))
    return _services


def reset_services() -> None:
    """Rebuild repository and services from seed data (used by tests and `make seed-demo`)."""
    global _services
    _services = None


def current_user(request: Request, services: Services = Depends(get_services)) -> AppUser:
    """POC only: the selected user is a cookie. V2 replaces this with Entra ID SSO."""
    return services.users.resolve(request.cookies.get(USER_COOKIE))
