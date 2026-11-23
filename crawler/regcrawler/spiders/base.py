"""Common base for the spiders: arguments, source from the database, snapshots, error recording.

Not a runnable spider (it has no `name`). The `rss`, `listing` and `unsc_xml` spiders extend it.

    scrapy crawl <spider> -a source=<source id> -a run_id=<crawl run id>

Scrapy 2.13+ start API: subclasses implement `async def start(self)`.
"""

from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import scrapy
from scrapy.exceptions import IgnoreRequest
from scrapy.http import Response
from scrapy.spidermiddlewares.httperror import HttpError
from twisted.python.failure import Failure

from app.repositories.base import Repository
from app.schemas.entities import Snapshot, Source
from app.schemas.enums import CheckChannel, CheckStatus
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
        fixture: str | None = None,
        dry_run: str | bool | None = None,
        source_json: str | None = None,
        out: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        # Dry run (the Sources page "Test this source"): the source comes from a JSON file (it may
        # be a proposed, not yet approved, configuration), one page is read, nothing is written to
        # the database or to the stored files; parsed rows go to the file named by `out`.
        self.dry_run = str(dry_run).lower() in ("1", "true", "yes") if dry_run else False
        self.out = Path(out) if out else None
        self.repo: Repository | None = None
        self.run: Any = None
        if self.dry_run:
            if not source_json:
                raise ValueError("A dry run needs -a source_json=<file> (the source to test)")
            self.source = Source.model_validate_json(Path(source_json).read_text(encoding="utf-8"))
        else:
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
        # Offline mode: `-a fixture=<file>` reads a saved file instead of the website. Everything
        # else (block detection, snapshot, pipelines) runs as usual; the snapshot URL shows file://.
        self.fixture = Path(fixture).expanduser().resolve() if fixture else None
        if self.fixture is not None:
            if not self.fixture.is_file():
                raise ValueError(f"fixture file not found: {fixture}")
            self.logger.warning("OFFLINE: reading %s instead of the website", self.fixture)

    @classmethod
    def from_crawler(cls, crawler: Any, *args: Any, **kwargs: Any) -> "BaseRegSpider":
        kwargs.setdefault("repository", crawler.settings.get("REGCRAWLER_REPOSITORY"))
        return super().from_crawler(crawler, *args, **kwargs)

    # --- helpers for subclasses ----------------------------------------------------------------

    @property
    def run_ref(self) -> str | None:
        """The crawl run id (None in a dry run, which belongs to no run)."""
        return self.run.id if self.run else None

    def target(self, url: str) -> str:
        """The URL to request: the real one, or the saved file in offline mode."""
        return self.fixture.as_uri() if self.fixture is not None else url

    def previous_snapshot(self, channel: CheckChannel) -> tuple[Snapshot, bytes] | None:
        """The latest earlier healthy snapshot of this source's channel, with its stored bytes.

        Offline (file://) and live snapshots are never mixed, so a fixture run cannot become the
        baseline of a live run, or the other way round.
        """
        if self.repo is None:  # dry run: nothing is stored, so there is nothing to compare with
            return None
        best: Snapshot | None = None
        for check in self.repo.list_source_checks():
            if (
                check.source_id != self.source.id
                or check.channel != channel
                or check.run_id == self.run.id
                or check.status != CheckStatus.OK
                or not check.snapshot_id
            ):
                continue
            snap = self.repo.get_snapshot(check.snapshot_id)
            if snap is None or (urlsplit(snap.url).scheme == "file") != (self.fixture is not None):
                continue
            if best is None or snap.fetched_at > best.fetched_at:
                best = snap
        if best is None:
            return None
        content = self.repo.read_blob(best.sha256)
        return None if content is None else (best, content)

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
            run_id=self.run_ref,
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
        if failure.check(HttpError):  # first: HttpError is itself an IgnoreRequest
            status = failure.value.response.status
            self.check.fail(channel, f"HTTP {status}", status)
        elif failure.check(IgnoreRequest):
            # Dropped on purpose: after a block (already recorded) or by robots.txt.
            if not self.check.blocked:
                self.check.fail(channel, str(failure.value) or "Request dropped")
        else:
            self.check.fail(channel, f"{failure.type.__name__}: {failure.getErrorMessage()}")
