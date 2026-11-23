"""The document download spider: outcomes per URL, nothing written to a database."""

import json

import pytest
from scrapy.exceptions import IgnoreRequest
from scrapy.http import HtmlResponse, Request
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.python.failure import Failure

from app.schemas.entities import Source
from app.schemas.enums import CheckChannel, SourceType
from regcrawler.spiders.docs import DocsSpider

URLS = ["https://example.test/a.pdf", "https://example.test/b.pdf", "https://example.test/c.pdf"]


@pytest.fixture
def spider(tmp_path) -> DocsSpider:
    source = Source(
        id="s", authority="X", page_name="Y", type=SourceType.HTML_LIST, url="https://example.test/"
    )
    (tmp_path / "source.json").write_text(source.model_dump_json(), encoding="utf-8")
    (tmp_path / "urls.json").write_text(json.dumps(URLS), encoding="utf-8")
    return DocsSpider(
        dry_run="1",
        source_json=str(tmp_path / "source.json"),
        urls_json=str(tmp_path / "urls.json"),
        out_dir=str(tmp_path / "files"),
        results=str(tmp_path / "results.json"),
    )


def request(index: int) -> Request:
    return Request(URLS[index], meta={"channel": "HTML", "index": index, "wanted": URLS[index]})


def results(spider) -> list[dict]:
    spider.closed("finished")
    return json.loads(spider.results_file.read_text(encoding="utf-8"))


def test_every_url_is_requested_in_order_with_its_index(spider) -> None:
    import asyncio

    async def collect():
        return [r async for r in spider.start()]

    requests = asyncio.run(collect())
    assert [r.url for r in requests] == URLS
    assert [r.meta["index"] for r in requests] == [0, 1, 2]
    assert all(r.meta["channel"] == "HTML" for r in requests)


def test_an_answer_is_saved_byte_for_byte(spider, tmp_path) -> None:
    response = HtmlResponse(
        URLS[0], body=b"%PDF-1.4 bytes", headers={"Content-Type": "application/pdf"},
        request=request(0),
    )  # fmt: skip
    spider.parse(response)
    (row, *_rest) = results(spider)
    assert row["status"] == "OK" and row["content_type"] == "application/pdf"
    assert (tmp_path / "files" / row["file"]).read_bytes() == b"%PDF-1.4 bytes"


def test_an_http_error_is_failed_with_its_status(spider) -> None:
    failure = Failure(HttpError(HtmlResponse(URLS[1], status=404, request=request(1))))
    failure.request = request(1)
    spider.on_error(failure)
    row = results(spider)[1]
    assert (row["status"], row["http_status"]) == ("FAILED", 404)


def test_a_block_marks_this_and_every_remaining_url_blocked(spider) -> None:
    spider.check.block(CheckChannel.HTML, "HTTP 403: access refused by the website", 403)
    failure = Failure(IgnoreRequest("HTTP 403: access refused by the website"))
    failure.request = request(1)
    spider.on_error(failure)
    rows = results(spider)
    assert rows[1]["status"] == "BLOCKED" and "403" in rows[1]["detail"]
    assert rows[2]["status"] == "BLOCKED" and "blocked earlier" in rows[2]["detail"]  # never asked


def test_a_robots_refusal_is_treated_as_a_manual_fetch(spider) -> None:
    failure = Failure(IgnoreRequest("Forbidden by robots.txt"))
    failure.request = request(2)
    spider.on_error(failure)
    row = results(spider)[2]
    assert row["status"] == "BLOCKED" and "robots.txt" in row["detail"]


def test_a_network_error_is_failed(spider) -> None:
    failure = Failure(TimeoutError("timed out"))
    failure.request = request(0)
    spider.on_error(failure)
    assert results(spider)[0]["status"] == "FAILED"


def test_urls_never_answered_are_failed_when_nothing_was_blocked(spider) -> None:
    assert [r["status"] for r in results(spider)] == ["FAILED"] * 3


def test_the_arguments_are_required(tmp_path) -> None:
    with pytest.raises(ValueError, match="urls_json"):
        DocsSpider(dry_run="1", source_json=str(tmp_path / "none.json"))
