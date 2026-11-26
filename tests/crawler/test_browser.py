"""The browser engine's rules that need no browser: availability, settings, launch, hint, policy."""

import importlib

import pytest

from app.schemas.enums import CheckStatus, Cycle, FetchEngine, SourceType
from app.services import source_test
from app.services.crawl_runner import CrawlRunner, LaunchResult, RunLock, SubprocessLauncher
from regcrawler import browser, settings
from tests.conftest import BACKENDS, make_backend
from tests.crawl_helpers import FakeLauncher
from tests.detection_helpers import make_source


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path, sql_template):
    repository, _url, _files = make_backend(request.param, tmp_path, sql_template)
    return repository


# --- missing scrapy-playwright or Chromium: a clear message that points to install-browser -------


def test_missing_scrapy_playwright_is_explained(monkeypatch) -> None:
    monkeypatch.setattr(browser, "scrapy_playwright_installed", lambda: False)
    reason = browser.unavailable_reason()
    assert "scrapy-playwright is not installed" in reason and "install-browser" in reason


def test_missing_chromium_is_explained(monkeypatch) -> None:
    monkeypatch.setattr(browser, "scrapy_playwright_installed", lambda: True)
    monkeypatch.setattr(browser, "chromium_installed", lambda: False)
    reason = browser.unavailable_reason()
    assert "Chromium is not installed" in reason and ".\\tasks.ps1 install-browser" in reason


def test_nothing_is_missing_when_both_are_there(monkeypatch) -> None:
    monkeypatch.setattr(browser, "scrapy_playwright_installed", lambda: True)
    monkeypatch.setattr(browser, "chromium_installed", lambda: True)
    assert browser.unavailable_reason() is None


def test_chromium_is_found_where_playwright_installs_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("PLAYWRIGHT_BROWSERS_PATH", str(tmp_path))
    assert browser.chromium_installed() is False
    (tmp_path / "ffmpeg-1013").mkdir()
    assert browser.chromium_installed() is False  # another browser's folder does not count
    (tmp_path / "chromium_headless_shell-1255").mkdir()
    assert browser.chromium_installed() is True


# --- settings: the browser is off unless this crawl is for a browser source -----------------------


def reload_settings(monkeypatch, engine: str | None):
    if engine is None:
        monkeypatch.delenv(browser.ENGINE_ENV, raising=False)
    else:
        monkeypatch.setenv(browser.ENGINE_ENV, engine)
    return importlib.reload(settings)


@pytest.fixture(autouse=True)
def _restore_settings(monkeypatch):
    yield
    monkeypatch.delenv(browser.ENGINE_ENV, raising=False)
    importlib.reload(settings)


def test_a_standard_crawl_never_loads_the_browser_handler(monkeypatch) -> None:
    s = reload_settings(monkeypatch, None)
    assert s.PLAYWRIGHT_ENABLED is False and not hasattr(s, "DOWNLOAD_HANDLERS")
    s = reload_settings(monkeypatch, "http")
    assert s.PLAYWRIGHT_ENABLED is False and not hasattr(s, "DOWNLOAD_HANDLERS")


def test_a_browser_crawl_is_plain_headless_and_as_polite_as_before(monkeypatch) -> None:
    for name in ("HTTPS_PROXY", "HTTP_PROXY"):
        monkeypatch.delenv(name, raising=False)
    s = reload_settings(monkeypatch, "browser")
    assert s.PLAYWRIGHT_ENABLED is True
    assert s.DOWNLOAD_HANDLERS["https"].endswith("ScrapyPlaywrightDownloadHandler")
    assert s.PLAYWRIGHT_BROWSER_TYPE == "chromium"
    assert s.PLAYWRIGHT_LAUNCH_OPTIONS == {"headless": True}
    assert s.PLAYWRIGHT_MAX_PAGES_PER_CONTEXT == 1 and s.PLAYWRIGHT_MAX_CONTEXTS == 1
    # the politeness rules are the same objects as for the standard engine
    assert s.ROBOTSTXT_OBEY is True and s.DOWNLOAD_DELAY >= 2
    assert s.CONCURRENT_REQUESTS_PER_DOMAIN == 1 and s.AUTOTHROTTLE_ENABLED is True
    forbidden = ("STEALTH", "FINGERPRINT", "ROTAT", "PROXY_LIST", "CAPTCHA")
    assert not [n for n in vars(s) if any(f in n.upper() for f in forbidden)]
    assert "user_agent" not in s.PLAYWRIGHT_LAUNCH_OPTIONS


def test_the_only_proxy_is_the_corporate_one_from_the_environment(monkeypatch) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://user:p%40ss@proxy.corp.example:3128")
    assert browser.proxy_option() == {
        "server": "http://proxy.corp.example:3128",
        "username": "user",
        "password": "p@ss",
    }
    monkeypatch.delenv("HTTPS_PROXY")
    monkeypatch.delenv("HTTP_PROXY", raising=False)
    assert browser.proxy_option() is None


# --- launching: only a browser source's listing crawl switches the browser on ---------------------


def test_the_runner_asks_for_the_browser_only_for_a_browser_html_listing(repo, tmp_path) -> None:
    make_source(repo, "web-std", rss_url="https://example.test/feed.xml")
    make_source(
        repo,
        "web-js",
        rss_url="https://example.test/feed2.xml",
        fetch_engine=FetchEngine.BROWSER,
    )
    launcher = FakeLauncher(repo)
    runner = CrawlRunner(repo, launcher, lock=RunLock(tmp_path / "crawl.lock"))
    runner.start(Cycle.ADHOC, "Divya G.", background=False)
    engines = {
        call: args["fetch_engine"] for call, args in zip(launcher.calls, launcher.args, strict=True)
    }
    assert engines[("web-js", "listing")] == "browser"
    assert engines[("web-js", "rss")] == "http"  # the RSS side is always plain HTTP
    assert engines[("web-std", "listing")] == "http"
    assert [e for (sid, _), e in engines.items() if e == "browser"] == ["browser"]


def test_the_launcher_sets_the_engine_for_a_browser_crawl_only(monkeypatch, tmp_path) -> None:
    seen = []

    def fake_run(command, **kwargs):
        seen.append(kwargs["env"].get(browser.ENGINE_ENV))

        class Done:
            returncode, stderr = 0, ""

        return Done()

    monkeypatch.setattr("app.services.crawl_runner.subprocess.run", fake_run)
    monkeypatch.setenv("FILES_ROOT", str(tmp_path))
    monkeypatch.delenv(browser.ENGINE_ENV, raising=False)
    launcher = SubprocessLauncher()
    launcher.launch("listing", "s1", "run-1")
    launcher.launch("listing", "s1", "run-1", fetch_engine=FetchEngine.BROWSER)
    assert seen == [None, "browser"]


def test_a_browser_source_that_leaves_no_result_points_to_the_install_target(
    repo, tmp_path
) -> None:
    make_source(repo, "web-js", rss_url=None, fetch_engine=FetchEngine.BROWSER)

    class Silent(FakeLauncher):
        def launch(self, spider, source_id, run_id, *a, **kw):
            if source_id == "web-js":
                return LaunchResult(1)  # the process died and wrote no check
            return super().launch(spider, source_id, run_id, *a, **kw)

    runner = CrawlRunner(repo, Silent(repo), lock=RunLock(tmp_path / "crawl.lock"))
    run = runner.start(Cycle.ADHOC, "Divya G.", "web-js", background=False)
    (check,) = [c for c in repo.list_source_checks(run.id) if c.source_id == "web-js"]
    assert check.status == CheckStatus.FAILED and "install-browser" in check.error_detail


# --- "Test this source": which engine, and the hint only for the standard engine ------------------


def payload(status: str) -> dict:
    return {"status": status, "rows_found": 0, "detail": ""}


@pytest.mark.parametrize(
    ("engine", "hint_expected"), [("http", True), ("browser", False)]
)
def test_the_browser_hint_is_shown_for_zero_items_only_with_the_standard_engine(
    repo, engine, hint_expected
) -> None:
    source = make_source(repo, "hint", fetch_engine=FetchEngine(engine))
    message = source_test._message(
        CheckStatus.ZERO_ITEMS, payload("ZERO_ITEMS"), source, FetchEngine(engine)
    )
    assert (source_test.NEEDS_BROWSER_HINT in message) is hint_expected
    assert "Try Fetch with: Browser." in source_test.NEEDS_BROWSER_HINT


def test_no_hint_for_other_results_or_other_ways_of_reading(repo) -> None:
    source = make_source(repo, "hint2")
    for status in (CheckStatus.OK, CheckStatus.BLOCKED, CheckStatus.FAILED):
        message = source_test._message(status, payload(status.value), source, FetchEngine.HTTP)
        assert source_test.NEEDS_BROWSER_HINT not in message
    rss = make_source(repo, "hint3", type=SourceType.RSS)
    message = source_test._message(
        CheckStatus.ZERO_ITEMS, payload("ZERO_ITEMS"), rss, FetchEngine.HTTP
    )
    assert source_test.NEEDS_BROWSER_HINT not in message


def test_the_live_tester_starts_the_browser_only_for_a_browser_source(
    repo, monkeypatch, tmp_path
) -> None:
    seen = []

    def fake_run(command, **kwargs):
        seen.append((kwargs.get("env") or {}).get(browser.ENGINE_ENV))

        class Done:
            returncode, stderr, stdout = 0, "", ""

        return Done()

    monkeypatch.setattr(source_test.subprocess, "run", fake_run)
    tester = source_test.SubprocessSourceTester(timeout=5)
    tester.run(make_source(repo, "t-std"))
    tester.run(make_source(repo, "t-js", fetch_engine=FetchEngine.BROWSER, wait_timeout_s=40))
    assert seen == [None, "browser"]
