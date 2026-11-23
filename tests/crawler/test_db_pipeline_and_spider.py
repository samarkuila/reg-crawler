"""DbPipeline writes through the repository; BaseRegSpider loads its source from the database."""

import pytest
from scrapy.exceptions import DropItem

from app.schemas.enums import CheckChannel, CheckStatus
from regcrawler.items import ListingItem
from regcrawler.pipelines import DbPipeline
from tests.crawler.conftest import SOURCE_ID, DemoSpider


def row(n: int, run_id: str) -> ListingItem:
    return ListingItem(
        source_id=SOURCE_ID,
        run_id=run_id,
        channel="HTML",
        title=f"Notice {n}",
        url=f"https://rbi.org.in/n/{n}",
        raw_extra={},
    )


def checks(repo, spider):
    return {c.channel: c for c in repo.list_source_checks(spider.run.id)}


def test_rows_and_an_ok_check_are_written_when_the_spider_closes(crawler, repo, spider) -> None:
    pipeline = DbPipeline(crawler, repo)
    spider.check.mark_attempted(CheckChannel.HTML, 200)
    for n in (1, 2, 3):
        pipeline.process_item(row(n, spider.run.id))
    assert repo.list_raw_items(spider.run.id) == []  # nothing is written until the end
    pipeline.close_spider()
    titles = [r.title for r in repo.list_raw_items(spider.run.id, SOURCE_ID)]
    assert titles == ["Notice 1", "Notice 2", "Notice 3"]
    check = checks(repo, spider)[CheckChannel.HTML]
    assert (check.status, check.items_found, check.http_status) == (CheckStatus.OK, 3, 200)


def test_an_empty_listing_is_zero_items_not_ok(crawler, repo, spider) -> None:
    spider.check.mark_attempted(CheckChannel.HTML, 200)
    DbPipeline(crawler, repo).close_spider()
    check = checks(repo, spider)[CheckChannel.HTML]
    assert (check.status, check.items_found) == (CheckStatus.ZERO_ITEMS, 0)


def test_a_block_is_recorded_as_blocked_without_a_count(crawler, repo, spider) -> None:
    spider.check.block(CheckChannel.HTML, "HTTP 403: access refused by the website", 403)
    DbPipeline(crawler, repo).close_spider()
    check = checks(repo, spider)[CheckChannel.HTML]
    assert check.status == CheckStatus.BLOCKED
    assert check.http_status == 403 and check.items_found is None
    assert "403" in check.error_detail
    assert repo.list_raw_items(spider.run.id) == []


def test_a_channel_not_fetched_because_of_an_earlier_block_is_blocked_too(
    crawler, repo, spider
) -> None:
    spider.check.expected = (CheckChannel.RSS, CheckChannel.HTML)
    spider.check.block(CheckChannel.RSS, "HTTP 403", 403)
    DbPipeline(crawler, repo).close_spider()
    by = checks(repo, spider)
    assert by[CheckChannel.HTML].status == CheckStatus.BLOCKED
    assert "earlier" in by[CheckChannel.HTML].error_detail


def test_a_channel_that_answered_before_the_block_keeps_its_own_result(
    crawler, repo, spider
) -> None:
    spider.check.expected = (CheckChannel.RSS, CheckChannel.HTML)
    spider.check.mark_attempted(CheckChannel.RSS, 200)
    spider.check.block(CheckChannel.HTML, "HTTP 429: slow down", 429)
    DbPipeline(crawler, repo).close_spider()
    by = checks(repo, spider)
    assert by[CheckChannel.RSS].status == CheckStatus.ZERO_ITEMS  # it answered, with nothing
    assert by[CheckChannel.HTML].status == CheckStatus.BLOCKED


def test_a_source_that_never_answered_is_failed(crawler, repo, spider) -> None:
    DbPipeline(crawler, repo).close_spider()  # e.g. robots.txt disallowed the page
    check = checks(repo, spider)[CheckChannel.HTML]
    assert check.status == CheckStatus.FAILED and "No page was fetched" in check.error_detail


def test_a_block_is_never_downgraded_to_a_plain_failure(crawler, repo, spider) -> None:
    spider.check.fail(CheckChannel.HTML, "HTTP 500", 500)
    spider.check.block(CheckChannel.HTML, "HTTP 403", 403)
    spider.check.fail(CheckChannel.HTML, "HTTP 500", 500)
    DbPipeline(crawler, repo).close_spider()
    assert checks(repo, spider)[CheckChannel.HTML].status == CheckStatus.BLOCKED


def test_a_row_without_a_url_is_dropped_and_counted_in_the_check(crawler, repo, spider) -> None:
    pipeline = DbPipeline(crawler, repo)
    spider.check.mark_attempted(CheckChannel.HTML, 200)
    pipeline.process_item(row(1, spider.run.id))
    with pytest.raises(DropItem):
        pipeline.process_item(
            ListingItem(
                source_id=SOURCE_ID, run_id=spider.run.id, channel="HTML", url="", title="x"
            )
        )
    pipeline.close_spider()
    check = checks(repo, spider)[CheckChannel.HTML]
    assert check.status == CheckStatus.OK and check.items_found == 1
    assert "1 row(s) without a URL" in check.error_detail


def test_every_write_has_an_audit_event_by_the_crawler(crawler, repo, spider) -> None:
    before = len(repo.list_audit())
    spider.check.mark_attempted(CheckChannel.HTML, 200)
    pipeline = DbPipeline(crawler, repo)
    pipeline.process_item(row(1, spider.run.id))
    pipeline.close_spider()
    new = repo.list_audit()[: len(repo.list_audit()) - before]
    assert {e.action for e in new} == {"raw_listing_items.recorded", "source_check.recorded"}
    assert {e.actor for e in new} == {"crawler"}


# --- spider arguments --------------------------------------------------------------------------


def test_the_source_comes_from_the_database(repo, spider) -> None:
    assert spider.source == repo.get_source(SOURCE_ID)
    assert spider.run == repo.get_run(spider.run.id)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({}, "Usage"),
        ({"source": SOURCE_ID}, "Usage"),
        ({"source": "no-such-source", "run_id": "x"}, "Unknown source"),
        ({"source": SOURCE_ID, "run_id": "no-such-run"}, "Unknown crawl run"),
    ],
)
def test_bad_arguments_are_refused(repo, kwargs, message) -> None:
    with pytest.raises(ValueError, match=message):
        DemoSpider(repository=repo, **kwargs)


def test_an_inactive_source_is_not_crawled(repo, spider) -> None:
    inactive = next(s for s in repo.list_sources() if not s.active)
    with pytest.raises(ValueError, match="not active"):
        DemoSpider(source=inactive.id, run_id=spider.run.id, repository=repo)
