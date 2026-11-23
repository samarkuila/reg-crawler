"""Common base for the spiders: arguments, source from the database, snapshots, error recording.

Not a runnable spider (it has no `name`). The `rss`, `listing` and `unsc_xml` spiders extend it.

    scrapy crawl <spider> -a source=<source id> -a run_id=<crawl run id>

Scrapy 2.13+ start API: subclasses implement `async def start(self)`.
"""

from typing import Any

import scrapy
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.python.failure import Failure

from app.repositories.base import Repository
from app.schemas.enums import CheckChannel
from regcrawler.items import SnapshotItem
from regcrawler.repo import get_repository
from regcrawler.state import CheckTracker


class BaseRegSpider(scrapy.Spider):
    channels: tuple[CheckChannel, ...] = ()  # channels this spider reads; each gets a source_check

    def __init__(
        self,
        source: str | None = None,
        run_id: str | None = None,
        repository: Repository | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        if not source or not run_id:
            raise ValueError(
                "Usage: scrapy crawl <spider> -a source=<source id> -a run_id=<run id>"
            )
        self.repo = repository or get_repository()
        found = self.repo.get_source(source)
        if found is None:
            raise ValueError(f"Unknown source {source!r}: it is not in the database")
        if not found.active:
            raise ValueError(
                f"Source {source!r} is not active (inactive, planned or awaiting approval); "
                "it is not crawled"
            )
        run = self.repo.get_run(run_id)
        if run is None:
            raise ValueError(f"Unknown crawl run {run_id!r}: create the run first")
        self.source = found  # loaded from the DB, never from a file
        self.run = run
        self.check = CheckTracker(expected=self.channels)

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> "BaseRegSpider":
        kwargs.setdefault("repository", crawler.settings.get("REGCRAWLER_REPOSITORY"))
        return super().from_crawler(crawler, *args, **kwargs)

    # --- helpers for subclasses ----------------------------------------------------------------

    def get(self, url: str, channel: CheckChannel, callback: Any, **meta: Any) -> scrapy.Request:
        """A plain GET with the channel noted (blocks and errors are recorded per channel)."""
        return scrapy.Request(
            url,
            callback=callback,
            errback=self.on_error,
            meta={"channel": channel.value, **meta},
            dont_filter=True,
        )

    def snapshot_of(self, response: Response, channel: CheckChannel) -> SnapshotItem:
        """The item that stores this response's raw bytes (yield it before the parsed rows)."""
        return SnapshotItem(
            source_id=self.source.id,
            run_id=self.run.id,
            channel=channel.value,
            url=response.url,
            body=response.body,
            http_status=response.status,
        )

    def on_error(self, failure: Failure) -> None:
        """Record a failed request. Blocks are recorded by the middleware, not here."""
        request = failure.request  # type: ignore[attr-defined]
        try:
            channel = CheckChannel(str(request.meta.get("channel", "HTML")).upper())
        except ValueError:
            channel = CheckChannel.HTML
        if failure.check(IgnoreRequest):
            # Dropped on purpose: after a block (already recorded) or by robots.txt.
            if not self.check.blocked:
                self.check.fail(channel, str(failure.value) or "Request dropped")
        elif failure.check(HttpError):
            status = failure.value.response.status
            self.check.fail(channel, f"HTTP {status}", status)
        else:
            self.check.fail(channel, f"{failure.type.__name__}: {failure.getErrorMessage()}")
