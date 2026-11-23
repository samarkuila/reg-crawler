"""API test setup: every test gets a fresh, seeded repository on each backend (no network)."""

import shutil

import pytest
from fastapi.testclient import TestClient

from app.api.deps import USER_COOKIE, get_services, reset_services
from app.main import app
from tests.conftest import BACKENDS

DIVYA, GEORGE, SAMARENDRA, AUDIT = "u-divya", "u-george", "u-samarendra", "u-audit"
EOD = "2026-11-20-EOD"
EGAZETTE_CHECK = "chk-2026-11-20-eod-egazette-html"


@pytest.fixture(autouse=True, params=BACKENDS)
def fresh_repo(request, monkeypatch: pytest.MonkeyPatch, tmp_path, sql_template) -> str:
    """Every API test runs twice: on the in-memory repository and on a fresh SQLite copy."""
    if request.param == "sql":
        folder = tmp_path / "sql"
        shutil.copytree(sql_template, folder)
        monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(folder / 'reg_monitor.db').as_posix()}")
        monkeypatch.setenv("FILES_ROOT", str(folder / "files"))
    monkeypatch.setenv("REPO_BACKEND", request.param)
    reset_services()
    yield request.param
    get_services().repo = None  # drop the connection to the temporary database
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
