"""Fetch engines end to end, offline: a real Scrapy subprocess reads pages served from localhost.

Fixture A (table_listing.html) is a static list read with the http engine. Fixture B
(js_listing.html) fills the same list with JavaScript after load and is read with the browser
engine using the SAME selectors. Nothing leaves this machine: a tiny server on 127.0.0.1 serves
tests/fixtures/html/generic/. These tests need Chromium (`.\\tasks.ps1 install-browser`); without it
they are skipped, and the missing-browser behaviour has its own tests in test_browser.py.
"""

import functools
import http.server
import threading

import pytest

from app.schemas.entities import Source
from app.schemas.enums import CheckStatus, FetchEngine
from app.services.source_test import NEEDS_BROWSER_HINT, SubprocessSourceTester
from regcrawler.browser import WAIT_MISSING_HINT, unavailable_reason
from tests.crawler.conftest import FIXTURES

pytestmark = pytest.mark.skipif(
    unavailable_reason() is not None,
    reason="Chromium is not installed (run .\\tasks.ps1 install-browser)",
)

SELECTORS = {
    "selectors": {"row": "tr.item", "title": "td a", "link": "td a", "date": "td.date"},
    "date_format": "%d-%m-%Y",
}
TITLES = [
    "RBI/2026-27/45 Know Your Customer Directions for NBFCs",
    "Auction of Government Securities",
    "Master Direction on Digital Lending",
]


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args) -> None:  # keep test output clean
        pass


@pytest.fixture(scope="module")
def base_url():
    handler = functools.partial(_Quiet, directory=str(FIXTURES / "html" / "generic"))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)  # localhost only
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture(autouse=True)
def _no_proxy(monkeypatch):
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setenv("no_proxy", "127.0.0.1,localhost")


def run(base_url: str, page: str, engine: str, **extra):
    source = Source(
        id=f"fixture-{engine}",
        authority="Fixture",
        page_name=page,
        type="HTML_LIST",
        url=f"{base_url}/{page}",
        adapter_config=SELECTORS,
        fetch_engine=engine,
        **extra,
    )
    return SubprocessSourceTester(timeout=120).run(source)


def test_static_list_with_the_standard_engine(base_url) -> None:
    result = run(base_url, "table_listing.html", "http")
    assert result.status == CheckStatus.OK and result.fetch_engine == FetchEngine.HTTP
    assert [r.title for r in result.rows] == TITLES
    assert NEEDS_BROWSER_HINT not in result.message


def test_javascript_list_with_the_browser_engine_uses_the_same_selectors(base_url) -> None:
    result = run(base_url, "js_listing.html", "browser")
    assert result.status == CheckStatus.OK and result.fetch_engine == FetchEngine.BROWSER
    assert [r.title for r in result.rows] == TITLES
    days = [str(r.displayed_date) for r in result.rows]
    assert days == ["2026-11-20", "2026-11-19", "2026-11-18"]
    assert result.rows[1].link.endswith("/docs/circular-102.pdf")
    static = run(base_url, "table_listing.html", "http")
    assert [(r.title, r.displayed_date) for r in result.rows] == [
        (r.title, r.displayed_date) for r in static.rows
    ]


def test_standard_engine_on_a_javascript_page_finds_nothing_and_suggests_the_browser(
    base_url,
) -> None:
    result = run(base_url, "js_listing.html", "http")
    assert result.status == CheckStatus.ZERO_ITEMS and result.rows == []
    assert NEEDS_BROWSER_HINT in result.message  # only the standard engine suggests this


def test_a_list_that_never_appears_is_zero_items_not_failed(base_url) -> None:
    result = run(base_url, "js_never_fills.html", "browser", wait_timeout_s=1)
    assert result.status == CheckStatus.ZERO_ITEMS and result.fetch_engine == FetchEngine.BROWSER
    assert WAIT_MISSING_HINT in result.message
    assert NEEDS_BROWSER_HINT not in result.message  # already in browser mode


def test_a_verification_page_in_browser_mode_is_blocked(base_url) -> None:
    """The verification text only exists after rendering; block detection sees the rendered page."""
    result = run(base_url, "js_verification.html", "browser", wait_timeout_s=1)
    assert result.status == CheckStatus.BLOCKED and result.rows == []
    assert "verification page" in result.message
