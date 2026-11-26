"""Listing spider: config-driven parsing on saved pages, filters, pagination, dry run.

The pages in tests/fixtures/html/generic/ are synthetic: they exercise the generic reader. Real
per-source pages come from the live samples (see docs/source-status.md).
"""

import asyncio
import json
from datetime import date

import pytest
from scrapy.http import HtmlResponse, Request

from app.schemas.enums import CheckChannel, CheckStatus, FetchEngine, SourceType
from regcrawler.pipelines import DbPipeline
from regcrawler.spiders.listing import ListingConfig, ListingConfigError, ListingSpider
from tests.crawler.conftest import (
    FIXTURES,
    check_of,
    crawl,
    fixture_bytes,
    make_spider,
    stub_crawler,
)

URL = "https://example-regulator.test/circulars"
TABLE = {
    "selectors": {
        "row": "tr.item",
        "title": "td a",
        "link": "td a",
        "date": "td.date",
        "ref_no_regex": r"RBI/\d{4}-\d{2}/\d+",
    },
    "date_format": "%d-%m-%Y",
    "max_pages": 1,
}
MINISTRY = {
    "selectors": {
        "row": "div.gz",
        "title": "a.gz-title",
        "link": "a.gz-title",
        "date": "span.gz-date",
        "ministry": "span.ministry",
    },
    "date_format": "%d-%m-%Y",
    "ministries": ["Finance", "Communications"],
    "always_keep": ["multiple ministries"],
}


def page(name: str, url: str = URL) -> HtmlResponse:
    return HtmlResponse(
        url,
        body=fixture_bytes("html", "generic", name),
        encoding="utf-8",
        headers={"Content-Type": "text/html; charset=utf-8"},
        request=Request(url, meta={"channel": "HTML"}),
    )


@pytest.fixture
def make(repo):
    """A listing spider for the demo source `trai`, with the given adapter_config applied."""

    def build(adapter: dict, **updates):
        spider = make_spider(ListingSpider, repo, "trai")
        spider.source = spider.source.model_copy(update={"adapter_config": adapter, **updates})
        spider.config = ListingConfig(adapter)
        return spider, stub_crawler(spider, repo)

    return build


def start_requests(spider) -> list:
    async def collect():
        return [r async for r in spider.start()]

    return asyncio.run(collect())


# --- the reading rules -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("adapter", "message"),
    [
        ({}, "row selector is not set"),
        ({"selectors": {"row": "tr"}}, "title selector is not set"),
        ({"selectors": {"row": "TODO-prompt-11", "title": "a"}}, "still a placeholder"),
        ({"selectors": {"row": "tr", "title": "a"}, "use_search_form": True}, "Search forms"),
    ],
)
def test_unusable_rules_are_rejected(adapter, message) -> None:
    with pytest.raises(ListingConfigError, match=message):
        ListingConfig(adapter)


def test_placeholder_selectors_give_a_parse_error_not_a_guess(repo) -> None:
    spider = make_spider(ListingSpider, repo, "trai")  # the seed's trai has no selectors
    assert start_requests(spider) == []
    DbPipeline(stub_crawler(spider, repo), repo).close_spider()
    check = check_of(repo, spider)
    assert check.status == CheckStatus.PARSE_ERROR and "selector" in check.error_detail


# --- reading rows ------------------------------------------------------------------------------


def test_rows_are_read_with_the_configured_selectors(make, repo) -> None:
    spider, crawler = make(TABLE)
    items = crawl(spider, page("table_listing.html"), crawler)
    assert len(items) == 3
    first = items[0]
    assert first["channel"] == "HTML"
    assert first["title"] == "RBI/2026-27/45 Know Your Customer Directions for NBFCs"
    assert first["url"] == "https://example-regulator.test/docs/circular-101.pdf"
    assert first["displayed_date"] == date(2026, 11, 20)
    assert first["ref_no"] == "RBI/2026-27/45"  # from selectors.ref_no_regex
    assert items[2]["title"] == "Master Direction on Digital Lending"  # whitespace collapsed
    check = check_of(repo, spider)
    assert (check.status, check.items_found) == (CheckStatus.OK, 3) and check.snapshot_id


def test_the_page_is_stored_unchanged_as_a_snapshot(make, repo) -> None:
    spider, crawler = make(TABLE)
    response = page("table_listing.html")
    crawl(spider, response, crawler)
    snap = repo.get_snapshot(check_of(repo, spider).snapshot_id)
    assert snap.channel == CheckChannel.HTML and repo.read_blob(snap.sha256) == response.body


def test_a_row_without_a_link_is_counted_not_stored(make, repo) -> None:
    spider, crawler = make({**TABLE, "selectors": {**TABLE["selectors"], "link": "td a.none"}})
    assert crawl(spider, page("table_listing.html"), crawler) == []
    check = check_of(repo, spider)
    assert "3 row(s) without a URL" in check.error_detail


def test_a_selector_that_matches_nothing_says_the_layout_may_have_changed(make, repo) -> None:
    spider, crawler = make({**TABLE, "selectors": {**TABLE["selectors"], "row": "tr.gone"}})
    assert crawl(spider, page("table_listing.html"), crawler) == []
    check = check_of(repo, spider)
    assert check.status == CheckStatus.ZERO_ITEMS and "layout" in check.error_detail


def test_an_invalid_selector_is_a_parse_error(make, repo) -> None:
    spider, crawler = make({**TABLE, "selectors": {**TABLE["selectors"], "row": "tr[[["}})
    assert crawl(spider, page("table_listing.html"), crawler) == []
    assert check_of(repo, spider).status == CheckStatus.PARSE_ERROR


# --- keywords are hints, never a filter (rule 3) -------------------------------------------------


def test_keywords_mark_rows_but_never_drop_them(make) -> None:
    spider, crawler = make(
        TABLE,
        include_keywords=["Know Your Customer", "digital lending"],
        exclude_keywords=["auction"],
    )
    items = crawl(spider, page("table_listing.html"), crawler)
    assert len(items) == 3  # the low-relevance auction row is still there
    assert items[0]["raw_extra"]["keywords_matched"] == ["Know Your Customer"]
    assert items[1]["raw_extra"]["exclude_hints"] == ["auction"]
    assert items[2]["raw_extra"]["keywords_matched"] == ["digital lending"]


# --- ministry filter ---------------------------------------------------------------------------


def test_the_ministry_filter_keeps_listed_ministries_and_weekly_gazettes(make, repo) -> None:
    spider, crawler = make(MINISTRY)
    items = crawl(spider, page("ministry_listing.html"), crawler)
    titles = [i["title"] for i in items]
    assert "Notification on fertiliser subsidy" not in titles  # Agriculture: out of scope
    assert titles == [
        "Notification on interest rates",
        "Weekly gazette, week 47",  # "multiple ministries" is always kept
        "Telecom (Amendment) Rules, 2026",
        "Notice without a ministry",  # cannot tell: kept, never silently dropped
    ]
    assert items[0]["raw_extra"]["ministry"] == "Ministry of Finance"
    check = check_of(repo, spider)
    assert check.status == CheckStatus.OK and check.items_found == 4
    assert "1 of 5 row(s) were outside the ministry filter" in check.error_detail


def test_no_ministry_filter_means_every_row_is_kept(make) -> None:
    spider, crawler = make({**MINISTRY, "ministries": []})
    assert len(crawl(spider, page("ministry_listing.html"), crawler)) == 5


def test_always_keep_wins_over_the_filter(make) -> None:
    spider, crawler = make({**MINISTRY, "ministries": ["Finance"]})
    titles = [i["title"] for i in crawl(spider, page("ministry_listing.html"), crawler)]
    assert "Weekly gazette, week 47" in titles


# --- pagination = lookback ---------------------------------------------------------------------


def paging(max_pages: int, **selectors) -> dict:
    return {**TABLE, "max_pages": max_pages, "selectors": {**TABLE["selectors"], **selectors}}


def test_the_next_page_is_requested_until_max_pages(make) -> None:
    spider, _ = make(paging(2, next="a.next"))
    spider.fixture = None  # a live-style run
    out = list(spider.parse(page("table_listing.html")))
    (request,) = [o for o in out if isinstance(o, Request)]
    assert request.url == "https://example-regulator.test/circulars?page=2"
    assert request.meta["channel"] == "HTML"
    assert (
        list(spider.parse(page("table_listing.html", URL + "?page=2")))[-1].__class__.__name__
        != "Request"
    )


def test_one_page_only_when_max_pages_is_one(make) -> None:
    spider, _ = make(paging(1, next="a.next"))
    assert not [o for o in spider.parse(page("table_listing.html")) if isinstance(o, Request)]


def test_max_pages_without_a_next_selector_reads_one_page_and_says_so(make, repo) -> None:
    spider, crawler = make(paging(3))
    crawl(spider, page("table_listing.html"), crawler)
    assert "no 'next' selector" in check_of(repo, spider).error_detail


# --- lookback_days: spiders accept it and listing spiders do not page past the window -----------


def live(spider, today: date, lookback: int | None = None) -> None:
    """Make the spider behave like a live run on a pinned day."""
    spider.fixture = None
    spider.today = lambda: today
    if lookback is not None:
        spider.lookback_days = lookback


def next_requests(spider, name: str = "table_listing.html") -> list:
    return [o for o in spider.parse(page(name)) if isinstance(o, Request)]


def test_lookback_days_defaults_to_seven_and_is_accepted(repo) -> None:
    assert make_spider(ListingSpider, repo, "trai").lookback_days == 7
    assert make_spider(ListingSpider, repo, "trai", lookback_days="3").lookback_days == 3
    assert make_spider(ListingSpider, repo, "trai", lookback_days=14).lookback_days == 14


@pytest.mark.parametrize("bad", ["abc", "0", "-2"])
def test_a_bad_lookback_days_is_refused(repo, bad) -> None:
    with pytest.raises(ValueError, match="lookback_days"):
        make_spider(ListingSpider, repo, "trai", lookback_days=bad)


def test_window_start_counts_today_as_day_one(repo) -> None:
    spider = make_spider(ListingSpider, repo, "trai", lookback_days=7)
    spider.today = lambda: date(2026, 11, 24)
    assert spider.window_start == date(2026, 11, 18)


def test_paging_continues_while_the_page_is_inside_the_window(make) -> None:
    spider, _ = make(paging(5, next="a.next"))
    live(spider, date(2026, 11, 24), 7)  # window starts 18.11.2026; page is 18-20.11
    assert len(next_requests(spider)) == 1


def test_paging_stops_when_the_whole_page_is_older_than_the_window(make, repo) -> None:
    spider, crawler = make(paging(5, next="a.next"))
    live(spider, date(2026, 12, 24), 7)  # window starts 18.12.2026; every row is older
    out = list(spider.parse(page("table_listing.html")))
    assert not [o for o in out if isinstance(o, Request)]
    assert len([o for o in out if o.__class__.__name__ == "ListingItem"]) == 3  # rows are kept
    assert spider.stopped_by_window and spider.pages_read == 1


def test_a_page_with_one_row_inside_the_window_keeps_paging(make) -> None:
    spider, _ = make(paging(5, next="a.next"))
    live(spider, date(2026, 11, 28), 9)  # window starts 20.11.2026; only the newest row is in
    assert len(next_requests(spider)) == 1


def test_a_page_without_readable_dates_does_not_stop_paging(make) -> None:
    cfg = paging(5, next="a.next")
    cfg["date_format"] = "%Y/%m/%d"  # does not match the page, so no date can be read
    spider, _ = make(cfg)
    live(spider, date(2030, 1, 1), 7)
    assert len(next_requests(spider)) == 1


def test_max_pages_stays_the_hard_cap_inside_the_window(make) -> None:
    spider, _ = make(paging(1, next="a.next"))
    live(spider, date(2026, 11, 24), 30)
    assert not next_requests(spider)


# --- date and section headings between the rows (date_from_header_row) -----------------------

HEADINGS = {
    "selectors": {"row": "tr.item", "title": "td a", "link": "td a", "header_row": "tr.head"},
    "date_format": "%b %d, %Y",
    "date_from_header_row": True,
}


def html(body: bytes) -> HtmlResponse:
    return HtmlResponse(URL, body=body, encoding="utf-8", request=Request(URL))


def test_heading_rows_give_each_item_its_date_and_section(make, repo) -> None:
    spider, crawler = make(HEADINGS)
    items = crawl(spider, page("master_directions_sections.html"), crawler)
    banks, nbfc = "Commercial Banks", "Non-Banking Financial Companies"
    assert [(i["displayed_date"], i["raw_extra"]["category"]) for i in items] == [
        (date(2026, 4, 30), banks),
        (date(2026, 4, 30), banks),
        (date(2026, 4, 2), banks),
        (date(2026, 4, 30), nbfc),  # the same date again in the next section
        (date(2026, 6, 18), nbfc),  # newer than the section before: no global order assumed
        (date(2026, 6, 18), nbfc),
    ]
    assert check_of(repo, spider).items_found == 6  # headings are not items


def test_a_heading_that_does_not_parse_is_a_section_not_a_date(make) -> None:
    spider, crawler = make(HEADINGS)
    crawl(spider, page("master_directions_sections.html"), crawler)
    assert spider.category == "Non-Banking Financial Companies"  # the latest label
    assert spider.header_date == "Jun 18, 2026"  # the latest date, as shown


def test_a_date_the_row_shows_wins_over_the_heading(make) -> None:
    adapter = {**HEADINGS, "selectors": {**HEADINGS["selectors"], "date": "td.when"}}
    spider, crawler = make(adapter)
    body = (
        b"<table><tr class='head'><td>Jan 05, 2026</td></tr>"
        b"<tr class='item'><td><a href='/a'>Own</a></td><td class='when'>Feb 01, 2026</td></tr>"
        b"<tr class='item'><td><a href='/b'>Head</a></td><td class='when'></td></tr></table>"
    )
    first, second = crawl(spider, html(body), crawler)
    assert (first["displayed_date"], second["displayed_date"]) == (
        date(2026, 2, 1),
        date(2026, 1, 5),
    )


def test_rows_before_any_date_heading_are_kept_and_reported(make, repo) -> None:
    spider, crawler = make(HEADINGS)
    body = b"<table><tr class='item'><td><a href='/a'>Early row</a></td></tr></table>"
    items = crawl(spider, html(body), crawler)
    assert len(items) == 1 and items[0]["displayed_date"] is None
    assert "category" not in items[0]["raw_extra"]
    assert "before any date heading" in check_of(repo, spider).error_detail


def test_without_the_option_heading_rows_change_nothing(make) -> None:
    plain = {k: v for k, v in HEADINGS.items() if k != "date_from_header_row"}
    spider, crawler = make(plain)
    items = crawl(spider, page("master_directions_sections.html"), crawler)
    assert [i["displayed_date"] for i in items] == [None] * 6
    assert all("category" not in i["raw_extra"] for i in items)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        ({"selectors": {"row": "tr.item", "title": "td a"}}, "header_row selector"),
        ({"date_format": ""}, "needs date_format"),
        ({"selectors": {"row": "tr.item", "title": "td a", "header_row": "TODO"}}, "placeholder"),
    ],
)
def test_date_from_header_row_needs_its_selector_and_date_format(change, message) -> None:
    with pytest.raises(ListingConfigError, match=message):
        ListingConfig({**HEADINGS, **change})


# --- requests ----------------------------------------------------------------------------------


def test_the_listing_url_is_requested(make) -> None:
    spider, _ = make(TABLE, url="https://example-regulator.test/circulars")
    (request,) = start_requests(spider)
    assert request.url == "https://example-regulator.test/circulars"
    assert "playwright" not in request.meta


def test_a_browser_source_fails_clearly_when_the_browser_is_missing(
    make, repo, monkeypatch
) -> None:
    from regcrawler.spiders import listing

    spider, crawler = make(TABLE, fetch_engine=FetchEngine.BROWSER)
    monkeypatch.setattr(
        listing,
        "unavailable_reason",
        lambda: "This source is fetched with the browser, but Chromium is not installed. "
        "Run .\\tasks.ps1 install-browser, then try again.",
    )
    assert start_requests(spider) == []  # nothing is fetched
    DbPipeline(crawler, repo).close_spider()
    check = check_of(repo, spider)
    assert check.status == CheckStatus.FAILED
    assert "install-browser" in check.error_detail and "nothing was fetched" in check.error_detail


def test_a_standard_source_never_uses_the_browser(make) -> None:
    spider, _ = make(TABLE, fetch_engine=FetchEngine.HTTP)
    (request,) = start_requests(spider)
    assert request.url == spider.source.url
    assert not {"playwright", "playwright_page_methods", "wait_outcome"} & set(request.meta)
    assert spider.engine_used == "http"


@pytest.mark.parametrize("kind", [SourceType.RSS, SourceType.STRUCTURED_DATA])
def test_the_browser_engine_is_only_for_an_html_listing(make, kind) -> None:
    """Even a stored browser setting on another way of reading is ignored by the crawler."""
    spider, _ = make(TABLE, type=kind, fetch_engine=FetchEngine.BROWSER)
    assert spider.source.uses_browser is False and spider.engine_used == "http"


def test_a_browser_request_waits_for_the_list_and_has_no_evasion_options(make, monkeypatch) -> None:
    from regcrawler.spiders import listing

    monkeypatch.setattr(listing, "unavailable_reason", lambda: None)
    monkeypatch.setattr(ListingSpider, "_playwright_ready", staticmethod(lambda: True))
    spider, _ = make(TABLE, fetch_engine=FetchEngine.BROWSER, wait_timeout_s=7)
    (request,) = start_requests(spider)
    assert request.meta["playwright"] is True
    (method,) = request.meta["playwright_page_methods"]
    page_fn, selector, timeout_ms, _outcome = (method.method, *method.args)
    assert selector == "tr.item"  # no wait selector set: the row selector
    assert timeout_ms == 7000 and page_fn is listing.wait_for_list
    forbidden = {"stealth", "proxy", "user_agent", "fingerprint", "playwright_context_kwargs"}
    assert not forbidden & set(request.meta)
    assert not forbidden & set(request.headers)
    spider2, _ = make(TABLE, fetch_engine=FetchEngine.BROWSER, wait_for_selector="div.loaded")
    (request2,) = start_requests(spider2)
    assert request2.meta["playwright_page_methods"][0].args[0] == "div.loaded"


def test_the_browser_engine_is_off_unless_the_crawl_was_started_with_it(make, repo, monkeypatch):
    from regcrawler.spiders import listing

    monkeypatch.setattr(listing, "unavailable_reason", lambda: None)
    monkeypatch.setattr(ListingSpider, "_playwright_ready", staticmethod(lambda: False))
    spider, crawler = make(TABLE, fetch_engine=FetchEngine.BROWSER)
    assert start_requests(spider) == []
    DbPipeline(crawler, repo).close_spider()
    assert "not started with the browser engine" in check_of(repo, spider).error_detail


def test_a_list_that_never_appears_is_zero_items_with_a_hint_not_failed(make, repo) -> None:
    spider, crawler = make(TABLE, fetch_engine=FetchEngine.BROWSER)
    response = page("table_listing.html").replace(
        body=b"<html><head><title>List</title></head><body><p>loading</p></body></html>"
    )
    response.request.meta["wait_outcome"] = {"found": False}  # the browser's wait timed out
    assert crawl(spider, response, crawler) == []
    check = check_of(repo, spider)
    assert check.status == CheckStatus.ZERO_ITEMS
    assert "page rendered but list not found; check selectors or wait selector" in (
        check.error_detail
    )
    assert check.snapshot_id  # the rendered page is kept as evidence


def test_a_rendered_page_is_parsed_with_the_same_selectors(make, repo) -> None:
    """The engine only changes how the page is fetched: the same rows come out."""
    http, http_crawler = make(TABLE, fetch_engine=FetchEngine.HTTP)
    rows_http = crawl(http, page("table_listing.html"), http_crawler)
    browser, browser_crawler = make(TABLE, fetch_engine=FetchEngine.BROWSER)
    response = page("table_listing.html")
    response.request.meta["wait_outcome"] = {"found": True}
    rows_browser = crawl(browser, response, browser_crawler)
    keys = ("title", "url", "ref_no", "displayed_date", "raw_extra")
    parsed = lambda rows: [{k: r.get(k) for k in keys} for r in rows]  # noqa: E731
    assert parsed(rows_browser) == parsed(rows_http) != []


# --- dry run: no database, no stored files -----------------------------------------------------


def dry_spider(tmp_path, adapter: dict):
    from app.schemas.entities import Source
    from app.schemas.enums import SourceType

    source = Source(
        id="trai-test",
        authority="TRAI",
        page_name="Press releases",
        type=SourceType.HTML_LIST,
        url=URL,
        adapter_config=adapter,
    )
    source_file = tmp_path / "source.json"
    source_file.write_text(source.model_dump_json(), encoding="utf-8")
    out = tmp_path / "out.json"
    return ListingSpider(dry_run="1", source_json=str(source_file), out=str(out)), out


def test_a_dry_run_needs_no_database_and_writes_only_the_result_file(tmp_path) -> None:
    from types import SimpleNamespace

    from scrapy.settings import Settings

    from regcrawler.pipelines import NormalisePipeline, SnapshotPipeline

    spider, out = dry_spider(tmp_path, TABLE)
    assert spider.repo is None and spider.run is None
    crawler = SimpleNamespace(spider=spider, settings=Settings())  # no repository anywhere
    snapshot, normalise, db = (
        SnapshotPipeline(crawler),
        NormalisePipeline(crawler),
        DbPipeline(crawler),
    )
    spider.check.mark_attempted(CheckChannel.HTML, 200)
    for item in spider.parse(page("table_listing.html")):
        db.process_item(normalise.process_item(snapshot.process_item(item)))
    db.close_spider()
    result = json.loads(out.read_text(encoding="utf-8"))
    assert result["status"] == "OK" and result["rows_found"] == 3
    assert result["rows"][0] == {
        "title": "RBI/2026-27/45 Know Your Customer Directions for NBFCs",
        "link": "https://example-regulator.test/docs/circular-101.pdf",
        "displayed_date": "2026-11-20",
        "ref_no": "RBI/2026-27/45",
    }
    assert db._repo is None and snapshot._repo is None  # the database was never opened


def test_a_dry_run_reads_one_page_even_if_more_are_configured(tmp_path) -> None:
    spider, _ = dry_spider(tmp_path, paging(5, next="a.next"))
    assert not [o for o in spider.parse(page("table_listing.html")) if isinstance(o, Request)]


def test_a_dry_run_needs_a_source_file() -> None:
    with pytest.raises(ValueError, match="source_json"):
        ListingSpider(dry_run="1")


# --- the real process, offline: scrapy crawl listing -a dry_run=1 -a fixture=... ---------------


def test_the_subprocess_tester_runs_the_real_spider_on_a_saved_page(tmp_path) -> None:
    from app.schemas.entities import Source
    from app.schemas.enums import SourceType
    from app.services.source_test import SubprocessSourceTester

    source = Source(
        id="trai-test",
        authority="TRAI",
        page_name="Press releases",
        type=SourceType.HTML_LIST,
        url=URL,
        adapter_config=TABLE,
    )
    fixture = FIXTURES / "html" / "generic" / "table_listing.html"
    result = SubprocessSourceTester(fixture=str(fixture)).run(source)
    assert result.status == CheckStatus.OK, result.message
    assert [r.ref_no for r in result.rows][0] == "RBI/2026-27/45"
    assert len(result.rows) == 3 and result.saved is False
    assert "Nothing was saved" in result.message


# --- first crawl window: paging stops at the window, request cap -------------------------------

OLD_PAGE = {**HEADINGS, "selectors": {**HEADINGS["selectors"], "next": "a.next"}, "max_pages": 5}


def first_run(spider, window: date | None, today: date = date(2026, 11, 24)) -> None:
    live(spider, today)
    spider.first_run = True
    spider.first_run_window = window


def test_the_first_crawl_stops_paging_after_a_page_that_is_all_older_than_the_window(make) -> None:
    spider, _ = make(OLD_PAGE)
    first_run(spider, date(2026, 11, 1))
    out = list(spider.parse(page("rbi_old_page.html")))
    assert not [o for o in out if isinstance(o, Request)]  # an "all old" page: no further page
    assert len([o for o in out if o.__class__.__name__ == "ListingItem"]) == 2  # rows are kept
    assert spider.stopped_by_window and spider.pages_read == 1


def test_the_first_crawl_keeps_paging_while_the_page_reaches_into_the_window(make) -> None:
    spider, _ = make(OLD_PAGE)
    first_run(spider, date(2026, 11, 1))
    out = list(spider.parse(page("rbi_monthly_headings.html")))  # newest rows are in the window
    assert len([o for o in out if isinstance(o, Request)]) == 1


def test_a_page_not_sorted_newest_first_is_read_up_to_max_pages(make) -> None:
    spider, _ = make(OLD_PAGE, sorted_newest_first=False)
    first_run(spider, date(2026, 11, 1))
    out = list(spider.parse(page("rbi_old_page.html")))
    assert len([o for o in out if isinstance(o, Request)]) == 1  # no early stop without order
    assert not spider.stopped_by_window


def test_mode_all_has_no_window_so_paging_is_not_cut_short(make) -> None:
    spider, _ = make(OLD_PAGE)
    first_run(spider, None)
    assert len([o for o in spider.parse(page("rbi_old_page.html")) if isinstance(o, Request)]) == 1


def test_the_request_cap_is_never_exceeded(make) -> None:
    spider, crawler = make({**OLD_PAGE, "max_pages": 100})
    first_run(spider, None)
    spider.request_cap = 2
    spider.requests_made = 1  # the first page request
    out = list(spider.parse(page("rbi_old_page.html")))
    assert len([o for o in out if isinstance(o, Request)]) == 1  # the 2nd request is allowed
    assert spider.requests_made == 2
    out = list(spider.parse(page("rbi_old_page.html", URL + "?page=2")))
    assert not [o for o in out if isinstance(o, Request)]  # the cap holds
    assert spider.requests_made == 2
    note = spider.check.state(spider.channels[0]).error_detail or ""
    assert "limit of 2 page requests" in note


def test_the_spider_never_asks_for_more_than_fifty_requests(repo) -> None:
    assert make_spider(ListingSpider, repo, "trai").request_cap == 50
    assert make_spider(ListingSpider, repo, "trai", max_requests="200").request_cap == 50
    assert make_spider(ListingSpider, repo, "trai", max_requests="7").request_cap == 7
