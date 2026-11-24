"""Item pipelines: Snapshot (raw bytes) -> Normalise -> DbPipeline.

All database access happens in DbPipeline, through app.repositories. Scrapy only fetches, snapshots
and parses: deciding what is new belongs to detection (CLAUDE.md rule 6, architecture §3.1).
"""

import hashlib
import json
import logging
import uuid
from typing import Any

from scrapy.exceptions import DropItem

from app.repositories.base import Repository
from app.schemas.entities import RawListingItem, Snapshot, SourceCheck, now_ist
from app.schemas.enums import CheckChannel
from app.services import normalise
from regcrawler.items import ListingItem, SnapshotItem
from regcrawler.repo import get_repository
from regcrawler.state import CheckTracker, outcome

logger = logging.getLogger(__name__)
ACTOR = "crawler"
DRY_RUN_MAX_ROWS = 50


def _id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:10]}"


def _channel(value: Any) -> CheckChannel:
    return CheckChannel(str(value).upper())


class SnapshotPipeline:
    """Stores the raw bytes of each fetched page, write-once, and records the snapshot row.

    File: data/snapshots/{source}/{date}/{sha256}. The same bytes on the same day share one file;
    a file that exists is never rewritten (rule 7).
    """

    def __init__(self, crawler: Any, repository: Repository | None = None) -> None:
        self.crawler = crawler
        self._repo = repository

    @property
    def repo(self) -> Repository:
        # Looked up on first use, so a dry run (which never writes) never opens the database.
        if self._repo is None:
            self._repo = get_repository(self.crawler.settings)
        return self._repo

    @classmethod
    def from_crawler(cls, crawler: Any) -> "SnapshotPipeline":
        return cls(crawler)

    def process_item(self, item: Any) -> Any:
        spider = self.crawler.spider
        if getattr(spider, "dry_run", False):
            if isinstance(item, SnapshotItem):
                del item["body"]  # a dry run stores nothing
            return item
        if isinstance(item, SnapshotItem):
            body: bytes = item["body"]
            digest = hashlib.sha256(body).hexdigest()
            fetched_at = now_ist()
            snapshot = Snapshot(
                id=_id("snap"),
                source_id=item["source_id"],
                channel=_channel(item["channel"]),
                url=item["url"],
                fetched_at=fetched_at,
                sha256=digest,
                path=f"data/snapshots/{item['source_id']}/{fetched_at.date().isoformat()}/{digest}",
            )
            self.repo.record_snapshot(snapshot, body, ACTOR)
            item["snapshot_id"] = snapshot.id
            del item["body"]  # the bytes are stored; do not carry them through the other pipelines
            spider.check.state(snapshot.channel).snapshot_id = snapshot.id
        elif isinstance(item, ListingItem) and not item.get("snapshot_id"):
            item["snapshot_id"] = spider.check.state(_channel(item["channel"])).snapshot_id
        return item


class NormalisePipeline:
    """Canonical URL, ISO date, reference number, clean title. Never drops an item."""

    def __init__(self, crawler: Any) -> None:
        self.crawler = crawler

    @classmethod
    def from_crawler(cls, crawler: Any) -> "NormalisePipeline":
        return cls(crawler)

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ListingItem):
            return item
        config = self.crawler.spider.source.adapter_config
        extra = dict(item.get("raw_extra") or {})

        raw_url = item.get("url") or ""
        url = normalise.canonical_url(raw_url) if raw_url else ""
        if url and url != raw_url:
            extra.setdefault("url_as_found", raw_url)
        item["url"] = url

        title = normalise.clean_text(item.get("title"))
        item["title"] = title or url

        raw_date = item.get("displayed_date")
        # `date_format` in a source's adapter_config describes its HTML pages only. Feeds and data
        # files have their own formats (RSS pubDate is RFC 822), so they use `rss_date_format` /
        # `xml_date_format` if a source sets one, and the general reader otherwise.
        channel = str(item.get("channel", "")).upper()
        date_format = (
            config.get("date_format")
            if channel == "HTML"
            else config.get(f"{channel.lower()}_date_format")
        )
        parsed = normalise.parse_date(raw_date, date_format)
        if raw_date and not (hasattr(raw_date, "isoformat") and parsed):
            extra.setdefault("displayed_date_raw", str(raw_date))
        item["displayed_date"] = parsed

        ref_no = normalise.normalise_ref_no(item.get("ref_no")) or normalise.find_ref_no(
            title, config.get("ref_no_regex") or (config.get("selectors") or {}).get("ref_no_regex")
        )
        item["ref_no"] = ref_no
        item["raw_extra"] = extra
        return item


class DbPipeline:
    """Writes raw listing rows and one source_check per channel when the spider closes.

    Rows are buffered and written in one transaction per channel, so a source either has all of a
    channel's rows and its check, or none of them.
    """

    def __init__(self, crawler: Any, repository: Repository | None = None) -> None:
        self.crawler = crawler
        self._repo = repository
        self.rows: dict[CheckChannel, list[RawListingItem]] = {}
        self.skipped: dict[CheckChannel, int] = {}
        self._row_no = 0  # page order, so "the first N undated rows" is well defined

    @property
    def repo(self) -> Repository:
        # Looked up on first use, so a dry run (which never writes) never opens the database.
        if self._repo is None:
            self._repo = get_repository(self.crawler.settings)
        return self._repo

    @classmethod
    def from_crawler(cls, crawler: Any) -> "DbPipeline":
        return cls(crawler)

    def process_item(self, item: Any) -> Any:
        if not isinstance(item, ListingItem):
            return item
        channel = _channel(item["channel"])
        if getattr(self.crawler.spider, "dry_run", False):
            if item.get("url"):
                self.rows.setdefault(channel, []).append(item)  # type: ignore[arg-type]
            else:
                self.skipped[channel] = self.skipped.get(channel, 0) + 1
            return item
        if not item.get("url"):
            self.skipped[channel] = self.skipped.get(channel, 0) + 1
            raise DropItem("Listing row without a URL cannot be identified; counted in the check")
        self._row_no += 1
        extra = dict(item.get("raw_extra") or {})
        extra["row_no"] = self._row_no
        self.rows.setdefault(channel, []).append(
            RawListingItem(
                id=_id("raw"),
                run_id=item["run_id"],
                source_id=item["source_id"],
                channel=channel,
                snapshot_id=item.get("snapshot_id"),
                title=item["title"],
                url=item["url"],
                ref_no=item.get("ref_no"),
                displayed_date=item.get("displayed_date"),
                raw_extra=extra,
                fetched_at=now_ist(),
            )
        )
        return item

    def close_spider(self) -> None:
        spider = self.crawler.spider
        tracker: CheckTracker = spider.check
        if getattr(spider, "dry_run", False):
            self._write_dry_run(spider, tracker)
            return
        for channel in {*tracker.expected, *tracker.channels, *self.rows}:
            rows = self.rows.get(channel, [])
            if rows:
                self.repo.add_raw_items(rows, ACTOR)
            self.repo.record_source_check(self._check(spider, tracker, channel, len(rows)), ACTOR)

    def _check(self, spider: Any, tracker: CheckTracker, channel: CheckChannel, found: int):  # type: ignore[no-untyped-def]
        result = outcome(tracker, channel, found, self.skipped.get(channel, 0))
        return SourceCheck(
            id=f"chk-{spider.run.id}-{spider.source.id}-{channel.value.lower()}",
            run_id=spider.run.id,
            source_id=spider.source.id,
            channel=channel,
            status=result.status,
            http_status=result.http_status,
            items_found=result.count,
            error_detail=result.detail,
            snapshot_id=tracker.state(channel).snapshot_id,
            stats={
                "rows_parsed": found,
                "pages_read": getattr(spider, "pages_read", None),
                "requests_made": getattr(spider, "requests_made", None),
                "window_start": (
                    spider.window_start.isoformat()
                    if getattr(spider, "window_start", None)
                    else None
                ),
            },
        )

    def _write_dry_run(self, spider: Any, tracker: CheckTracker) -> None:
        """Dry run: no database, no stored files. Parsed rows go to the file named by -a out=."""
        channel = spider.channels[0]
        rows = self.rows.get(channel, [])
        result = outcome(tracker, channel, len(rows), self.skipped.get(channel, 0))
        payload = {
            "status": result.status.value,
            "http_status": result.http_status,
            "detail": result.detail,
            "rows_found": len(rows),
            "rows": [
                {
                    "title": r["title"],
                    "link": r["url"],
                    "displayed_date": r["displayed_date"].isoformat()
                    if r.get("displayed_date")
                    else None,
                    "ref_no": r.get("ref_no"),
                }
                for r in rows[:DRY_RUN_MAX_ROWS]
            ],
        }
        if spider.out is not None:
            spider.out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
