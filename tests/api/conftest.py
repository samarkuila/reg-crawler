"""API test setup: every test gets a fresh, seeded in-memory repository (no network, no files)."""

import pytest
from fastapi.testclient import TestClient

from app.api.deps import USER_COOKIE, get_services, reset_services
from app.main import app

DIVYA, GEORGE, SAMARENDRA, AUDIT = "u-divya", "u-george", "u-samarendra", "u-audit"
EOD = "2026-11-20-EOD"
EGAZETTE_CHECK = "chk-2026-11-20-eod-egazette-html"


@pytest.fixture(autouse=True)
def fresh_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("REPO_BACKEND", "memory")
    reset_services()
    yield
    reset_services()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def as_user(client: TestClient, user_id: str) -> TestClient:
    client.cookies.set(USER_COOKIE, user_id)
    return client


@pytest.fixture
def audit_count():
    """Number of audit events right now (for 'this mutation wrote an audit event' checks)."""

    def count() -> int:
        return len(get_services().repo.list_audit())

    return count


ACCEPT_IORS = {
    "action": "accept",
    "treatment": "INFO_ONLY",
    "include_in_update": True,
    "applicability": "YES",
    "status_colour": "YET_TO_START",
    "impact_text": "The updated FAQs explain the Inter-operable Regulatory Sandbox.",
}
