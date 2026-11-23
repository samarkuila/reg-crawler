"""Listing spider: config-driven parsing on saved pages, filters, pagination, dry run.

The pages in tests/fixtures/html/generic/ are synthetic: they exercise the generic reader. Real
per-source pages come from the live samples (see docs/source-status.md).
"""

import asyncio
import json
from datetime import date

import pytest
from scrapy.http import HtmlResponse, Request

from app.schemas.enums import CheckChannel, CheckStatus
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


# --- requests ----------------------------------------------------------------------------------


def test_the_listing_url_is_requested(make) -> None:
    spider, _ = make(TABLE, url="https://example-regulator.test/circulars")
    (request,) = start_requests(spider)
    assert request.url == "https://example-regulator.test/circulars"
    assert "playwright" not in request.meta


def test_a_source_that_needs_a_browser_fails_clearly_when_there_is_none(make, repo) -> None:
    spider, crawler = make(TABLE, requires_js=True)
    assert start_requests(spider) == []  # scrapy-playwright is not installed or enabled
    DbPipeline(crawler, repo).close_spider()
    check = check_of(repo, spider)
    assert check.status == CheckStatus.FAILED and "requires_js" in check.error_detail


def test_a_browser_request_has_no_evasion_options(make, monkeypatch) -> None:
    spider, _ = make(TABLE, requires_js=True)
    monkeypatch.setattr(ListingSpider, "_playwright_ready", staticmethod(lambda: True))
    (request,) = start_requests(spider)
    assert request.meta["playwright"] is True
    forbidden = {"stealth", "proxy", "user_agent", "fingerprint", "playwright_context_kwargs"}
    assert not forbidden & set(request.meta)
    assert not forbidden & set(request.headers)


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
