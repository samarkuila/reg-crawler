"""RSS spider on saved feeds: items, description HTML, empty / malformed / HTTP error outcomes."""

import asyncio
from datetime import date
from pathlib import Path

import pytest
from scrapy.http import Request, TextResponse
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.python.failure import Failure

from app.schemas.enums import CheckChannel, CheckStatus
from regcrawler.pipelines import DbPipeline
from regcrawler.spiders.rss import RssSpider
from tests.crawler.conftest import (
    FIXTURES,
    check_of,
    crawl,
    file_response,
    make_spider,
    stub_crawler,
)

URL = "https://rbi.org.in/notifications_rss.xml"
SAMPLE = "rss/rbi_notifications_sample.xml"


def start_requests(spider) -> list:
    async def collect():
        return [r async for r in spider.start()]

    return asyncio.run(collect())


@pytest.fixture
def rss(repo):
    spider = make_spider(RssSpider, repo, "rbi")
    return spider, stub_crawler(spider, repo)


def test_every_entry_becomes_an_rss_listing_item(rss, repo) -> None:
    spider, crawler = rss
    items = crawl(spider, file_response(SAMPLE, URL), crawler)
    assert len(items) == 3
    first = items[0]
    assert first["channel"] == "RSS"
    assert first["title"].startswith("Reserve Bank of India (Non-Banking Financial Companies")
    assert first["url"] == "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13001&Mode=0"
    assert first["displayed_date"] == date(2026, 11, 20)
    assert first["raw_extra"]["guid"] == "rbi-notification-13001"
    assert first["raw_extra"]["pub_date_raw"] == "Fri, 20 Nov 2026 17:45:00 +0530"
    check = check_of(repo, spider)
    assert (check.status, check.items_found) == (CheckStatus.OK, 3)
    assert check.snapshot_id


def test_the_full_description_html_is_kept_as_document_text(rss) -> None:
    spider, crawler = rss
    items = crawl(spider, file_response(SAMPLE, URL), crawler)
    html = items[0]["raw_extra"]["description_html"]
    assert "<b>01.12.2026</b>" in html and "RBI/2026-27/45" in html
    assert "<table>" in items[2]["raw_extra"]["description_html"]


def test_tracking_parameters_are_dropped_from_the_link(rss) -> None:
    spider, crawler = rss
    items = crawl(spider, file_response(SAMPLE, URL), crawler)
    assert items[1]["url"] == "https://www.rbi.org.in/Scripts/NotificationUser.aspx?Id=13002&Mode=0"
    assert "utm_source" in items[1]["raw_extra"]["url_as_found"]


def test_a_low_relevance_row_is_still_kept(rss) -> None:
    spider, crawler = rss
    items = crawl(spider, file_response(SAMPLE, URL), crawler)
    assert "Auction of Government of India Dated Securities" in [i["title"] for i in items]


def test_the_raw_feed_is_stored_unchanged_as_a_snapshot(rss, repo) -> None:
    spider, crawler = rss
    response = file_response(SAMPLE, URL)
    crawl(spider, response, crawler)
    snap = repo.get_snapshot(check_of(repo, spider).snapshot_id)
    assert snap.channel == CheckChannel.RSS and snap.url == URL
    assert repo.read_blob(snap.sha256) == response.body


def test_a_valid_feed_with_no_entries_is_zero_items(rss, repo) -> None:
    spider, crawler = rss
    assert crawl(spider, file_response("rss/empty_feed.xml", URL), crawler) == []
    assert check_of(repo, spider).status == CheckStatus.ZERO_ITEMS


def test_a_truncated_feed_keeps_the_entries_it_could_read(rss, repo) -> None:
    spider, crawler = rss
    items = crawl(spider, file_response("rss/malformed_feed.xml", URL), crawler)
    assert items[0]["title"] == "Master Direction on KYC"
    check = check_of(repo, spider)
    assert check.status == CheckStatus.OK and "XML errors" in check.error_detail


@pytest.mark.parametrize("name", ["rss/unreadable_feed.xml", "rss/not_a_feed.html"])
def test_something_that_is_not_a_feed_is_a_parse_error(rss, repo, name) -> None:
    spider, crawler = rss
    assert crawl(spider, file_response(name, URL), crawler) == []
    check = check_of(repo, spider)
    assert check.status == CheckStatus.PARSE_ERROR and check.error_detail
    assert check.snapshot_id  # the bad answer is kept as evidence


def test_an_empty_body_is_a_parse_error(rss, repo) -> None:
    spider, crawler = rss
    response = TextResponse(URL, body=b"  \n", request=Request(URL, meta={"channel": "RSS"}))
    crawl(spider, response, crawler)
    check = check_of(repo, spider)
    assert check.status == CheckStatus.PARSE_ERROR and "empty" in check.error_detail


def test_an_http_error_is_failed(rss, repo) -> None:
    spider, crawler = rss
    response = file_response("rss/empty_feed.xml", URL, status=500)
    failure = Failure(HttpError(response))
    failure.request = Request(URL, meta={"channel": "RSS"})
    spider.on_error(failure)
    DbPipeline(crawler, repo).close_spider()
    check = check_of(repo, spider)
    assert (check.status, check.http_status) == (CheckStatus.FAILED, 500)
    assert check.items_found is None


def test_a_source_without_an_rss_url_is_failed_not_crashed(repo) -> None:
    spider = make_spider(RssSpider, repo, "unsc")  # a source with no rss_url
    assert start_requests(spider) == []
    DbPipeline(stub_crawler(spider, repo), repo).close_spider()
    check = check_of(repo, spider)
    assert check.status == CheckStatus.FAILED and "no rss_url" in check.error_detail


def test_offline_mode_requests_the_saved_file_not_the_website(repo) -> None:
    fixture = FIXTURES / "rss" / "empty_feed.xml"
    spider = make_spider(RssSpider, repo, "rbi", fixture=str(Path(fixture)))
    (request,) = start_requests(spider)
    assert request.url.startswith("file:///") and request.url.endswith("empty_feed.xml")


def test_online_mode_requests_the_source_feed(repo) -> None:
    (request,) = start_requests(make_spider(RssSpider, repo, "rbi"))
    assert request.url == URL and request.meta["channel"] == "RSS"


def test_a_missing_fixture_file_is_refused(repo) -> None:
    with pytest.raises(ValueError, match="fixture file not found"):
        make_spider(RssSpider, repo, "rbi", fixture="no/such/file.xml")
