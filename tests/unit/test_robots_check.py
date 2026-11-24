"""robots.txt pre-check: a disallowed source is recorded BLOCKED and never launched."""

import pytest

from app.schemas.enums import CheckStatus, Cycle
from app.services.crawl_runner import CrawlRunner, RunLock
from app.services.robots import RobotsCheck
from tests.conftest import BACKENDS, make_backend
from tests.crawl_helpers import FakeLauncher

UA = "TVSCredit-RegMonitor/0.1 (+test@example.invalid)"


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path, sql_template):
    repository, _url, _files = make_backend(request.param, tmp_path, sql_template)
    return repository


def check(status: int, body: str = "") -> RobotsCheck:
    return RobotsCheck(UA, fetch=lambda url: (status, body))


def test_disallowed_path_is_blocked_and_others_allowed() -> None:
    rc = check(200, "User-agent: *\nDisallow: /private/\n")
    assert rc.blocked_reason("https://a.gov.in/private/list") is not None
    assert rc.blocked_reason("https://a.gov.in/public/list") is None


def test_missing_robots_file_allows() -> None:
    assert check(404).blocked_reason("https://a.gov.in/x") is None


def test_forbidden_robots_file_blocks_the_site() -> None:
    assert "HTTP 403" in check(403).blocked_reason("https://a.gov.in/x")


def test_unreachable_site_is_left_to_the_crawler() -> None:
    def boom(url: str):
        raise OSError("down")

    assert RobotsCheck(UA, fetch=boom).blocked_reason("https://a.gov.in/x") is None


def test_robots_file_is_downloaded_once_per_website() -> None:
    calls: list[str] = []

    def fetch(url: str):
        calls.append(url)
        return 200, "User-agent: *\nAllow: /\n"

    rc = RobotsCheck(UA, fetch=fetch)
    rc.blocked_reason("https://a.gov.in/1")
    rc.blocked_reason("https://a.gov.in/2")
    assert calls == ["https://a.gov.in/robots.txt"]


def test_runner_records_blocked_and_does_not_launch(repo, tmp_path) -> None:
    launcher = FakeLauncher(repo)
    rules = "User-agent: *\nDisallow: /\n"
    runner = CrawlRunner(
        repo,
        launcher,
        lock=RunLock(tmp_path / "crawl.lock"),
        robots_factory=lambda: check(200, rules),
    )
    run = runner.start(Cycle.ADHOC, "Divya G.", background=False)
    assert launcher.calls == []  # nothing was requested
    checks = repo.list_source_checks(run.id)
    assert checks and all(c.status == CheckStatus.BLOCKED for c in checks)
    assert all("robots.txt" in (c.error_detail or "") for c in checks)
