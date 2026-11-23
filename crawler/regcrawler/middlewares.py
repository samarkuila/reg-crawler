"""Block detection (CLAUDE.md rule 1, policy W1).

When a site answers with 403/429, a CAPTCHA or bot-check page, or sends us to a login, we do three
things and nothing else: record the source check as BLOCKED, stop sending requests for that source,
and leave it to a person (manual-fetch path). We never retry a block, change headers, cookies or
proxies, or try to get past it.
"""

import logging
from typing import Any
from urllib.parse import urlsplit

from scrapy import Request
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response, TextResponse

from app.schemas.enums import CheckChannel
from regcrawler.config import CrawlerConfig, load_config

logger = logging.getLogger(__name__)

SCAN_BYTES = 64 * 1024
# Real listing pages are large; challenge and bot-check pages are small. A marker word in a big
# page (for example a search form that mentions a captcha) is therefore not treated as a block
# unless it is in the page title.
SMALL_PAGE_BYTES = 32 * 1024


def request_channel(request: Request) -> CheckChannel:
    try:
        return CheckChannel(str(request.meta.get("channel", "HTML")).upper())
    except ValueError:
        return CheckChannel.HTML


def _title(text: str) -> str:
    start = text.find("<title")
    if start == -1:
        return ""
    start = text.find(">", start) + 1
    end = text.find("</title>", start)
    return text[start:end] if end != -1 else ""


def block_reason(request: Request, response: Response, config: CrawlerConfig) -> str | None:
    """Why this response is a block, or None when it is an ordinary page."""
    if response.status in config.block_status_codes:
        return {
            401: "HTTP 401: the page asks for a login",
            403: "HTTP 403: access refused by the website",
            429: "HTTP 429: the website asked us to slow down or stop",
        }.get(response.status, f"HTTP {response.status}: access refused by the website")

    # Sent to a login page by a redirect (the redirect middleware has already followed it).
    redirected = request.meta.get("redirect_urls") or []
    if redirected:
        final_path = urlsplit(response.url).path.lower()
        first_path = urlsplit(redirected[0]).path.lower()
        for marker in config.login_url_markers:
            if marker in final_path and marker not in first_path:
                return f"Redirected to a login page ({urlsplit(response.url).path})"

    if not isinstance(response, TextResponse):
        return None
    content_type = (response.headers.get("Content-Type") or b"").decode("latin-1").lower()
    if "html" not in content_type and not response.text.lstrip().lower().startswith("<!doctype"):
        return None  # XML, JSON or PDF: not a bot-check page
    sample = response.text[:SCAN_BYTES].lower()
    for marker in config.block_markers:
        if marker in _title(sample):
            return f"Bot-check or challenge page (title mentions '{marker}')"
        if marker in sample and (
            len(response.body) < SMALL_PAGE_BYTES or response.status in (503, 520, 521, 522)
        ):
            return f"Bot-check or challenge page (page mentions '{marker}')"
    return None


class BlockDetectionMiddleware:
    """Downloader middleware: classify blocks, mark the source BLOCKED, stop the source."""

    def __init__(self, crawler: Any, config: CrawlerConfig | None = None) -> None:
        self.crawler = crawler
        self.config = config or load_config()

    @classmethod
    def from_crawler(cls, crawler: Any) -> "BlockDetectionMiddleware":
        return cls(crawler)

    @property
    def _tracker(self):  # type: ignore[no-untyped-def]
        spider = self.crawler.spider
        return getattr(spider, "check", None)

    def process_request(self, request: Request) -> None:
        tracker = self._tracker
        if tracker is not None and tracker.blocked:
            raise IgnoreRequest(f"Source already blocked in this run; not requesting {request.url}")

    def process_response(self, request: Request, response: Response) -> Response:
        tracker = self._tracker
        if tracker is None:
            return response
        reason = block_reason(request, response, self.config)
        if reason is None:
            tracker.mark_attempted(request_channel(request), response.status)
            return response
        tracker.block(request_channel(request), reason, response.status)
        logger.warning(
            "BLOCKED %s (%s): %s. Stopping this source; a person must fetch it manually.",
            getattr(self.crawler.spider, "name", "?"),
            request.url,
            reason,
        )
        raise IgnoreRequest(reason)
