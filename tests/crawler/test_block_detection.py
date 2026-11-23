"""BlockDetectionMiddleware: 403/429, bot-check pages, login redirects -> BLOCKED, stop."""

import pytest
from scrapy import Request
from scrapy.exceptions import IgnoreRequest

from app.schemas.enums import CheckChannel, CheckStatus
from regcrawler.middlewares import BlockDetectionMiddleware, block_reason
from tests.crawler.conftest import fixture_bytes, html_response


@pytest.fixture
def middleware(crawler, config) -> BlockDetectionMiddleware:
    return BlockDetectionMiddleware(crawler, config)


def request(url: str = "https://rbi.org.in/notifications", **meta) -> Request:
    return Request(url, meta={"channel": "HTML", **meta})


@pytest.mark.parametrize("status", [403, 429, 401])
def test_blocking_status_codes_are_blocks(middleware, spider, status) -> None:
    req = request()
    with pytest.raises(IgnoreRequest):
        middleware.process_response(req, html_response(b"nope", status=status, request=req))
    state = spider.check.state(CheckChannel.HTML)
    assert state.status == CheckStatus.BLOCKED
    assert state.http_status == status
    assert spider.check.blocked


@pytest.mark.parametrize("name", ["captcha_challenge.html", "cloudflare_challenge.html"])
def test_challenge_pages_are_blocks_even_with_status_200(middleware, spider, name) -> None:
    req = request()
    response = html_response(fixture_bytes("html", name), request=req)
    with pytest.raises(IgnoreRequest):
        middleware.process_response(req, response)
    assert spider.check.state(CheckChannel.HTML).status == CheckStatus.BLOCKED


def test_redirect_to_a_login_page_is_a_block(middleware, spider) -> None:
    req = request(redirect_urls=["https://rbi.org.in/notifications"])
    response = html_response(
        fixture_bytes("html", "login_page.html"),
        url="https://rbi.org.in/login?next=/x",
        request=req,
    )
    with pytest.raises(IgnoreRequest):
        middleware.process_response(req, response)
    assert "login" in spider.check.state(CheckChannel.HTML).error_detail.lower()


def test_a_page_that_is_a_login_by_its_own_address_is_not_a_redirect_block(config) -> None:
    # Only a redirect INTO a login page counts; a source whose real address contains the marker
    # (and was not redirected) is judged on its content like any other page.
    req = request("https://example.org/auth/notices")
    response = html_response(b"<html><title>Notices</title>" + b"x" * 40_000, req.url, request=req)
    assert block_reason(req, response, config) is None


def test_a_normal_listing_is_not_a_block_even_if_it_mentions_captcha(middleware, spider) -> None:
    # The fixture mentions "captcha" in a comment; real listing pages are far bigger than a
    # challenge page, so pad it like one.
    body = fixture_bytes("html", "rbi_listing_sample.html") + b"<!-- -->" + b" " * 40_000
    req = request()
    response = html_response(body, request=req)
    assert middleware.process_response(req, response) is response
    assert not spider.check.blocked


def test_xml_feeds_are_never_scanned_for_markers(config) -> None:
    req = request("https://rbi.org.in/feed.xml", channel="RSS")
    response = html_response(
        b"<rss><channel><title>captcha rules</title></channel></rss>",
        req.url,
        request=req,
        content_type="application/rss+xml",
    )
    assert block_reason(req, response, config) is None


def test_after_a_block_no_further_request_for_the_source_is_sent(middleware, spider) -> None:
    req = request()
    with pytest.raises(IgnoreRequest):
        middleware.process_response(req, html_response(b"", status=429, request=req))
    with pytest.raises(IgnoreRequest, match="already blocked"):
        middleware.process_request(request("https://rbi.org.in/other"))


def test_requests_pass_before_any_block(middleware) -> None:
    assert middleware.process_request(request()) is None


def test_a_block_is_never_retried_by_scrapy_settings() -> None:
    from regcrawler import settings

    assert 403 not in settings.RETRY_HTTP_CODES and 429 not in settings.RETRY_HTTP_CODES
    # our middleware (560) sees a response before the retry middleware (550) does
    assert settings.DOWNLOADER_MIDDLEWARES["regcrawler.middlewares.BlockDetectionMiddleware"] > 550
