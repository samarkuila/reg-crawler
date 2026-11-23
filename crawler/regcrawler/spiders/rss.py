"""RSS spider: reads a source's `rss_url` feed.

    scrapy crawl rss -a source=<source id> -a run_id=<run id> [-a fixture=<saved feed file>]

One ListingItem (channel RSS) per feed entry: title, link, pubDate, guid, and the full description
HTML (RBI notifications carry the notice text there) in raw_extra["description_html"]. The raw feed
bytes are stored as a snapshot first, so the original is always available as evidence.

Outcomes: entries found -> OK; valid feed with no entries -> ZERO_ITEMS; not a readable feed ->
PARSE_ERROR; HTTP error / no answer -> FAILED; block -> BLOCKED (middleware).
"""

from collections.abc import Iterator
from typing import Any

import feedparser
from scrapy.http import Response

from app.schemas.enums import CheckChannel
from regcrawler.items import ListingItem
from regcrawler.spiders.base import BaseRegSpider

CHANNEL = CheckChannel.RSS
# feedparser flags these as "bozo" although the feed is fine.
_BENIGN = ("CharacterEncodingOverride",)


class RssSpider(BaseRegSpider):
    name = "rss"
    channels = (CHANNEL,)

    async def start(self) -> Any:
        if not self.source.rss_url:
            self.check.fail(CHANNEL, "This source has no rss_url")
            return
        yield self.get(self.target(self.source.rss_url), CHANNEL, self.parse)

    def parse(self, response: Response) -> Iterator[Any]:
        yield self.snapshot_of(response, CHANNEL)  # the original bytes first, whatever follows
        if not response.body.strip():
            self.check.parse_error(CHANNEL, "The feed answered with an empty body")
            return
        feed = feedparser.parse(response.body)
        if not feed.get("version") and not feed.entries:
            # feedparser reads any web page as an empty feed; no version means it is not a feed.
            self.check.parse_error(CHANNEL, "The answer is not an RSS or Atom feed")
            return
        error = feed.get("bozo_exception")
        if feed.get("bozo") and error is not None and not feed.entries:
            self.check.parse_error(CHANNEL, f"Not a readable feed: {_why(error)}")
            return
        if feed.get("bozo") and error is not None and type(error).__name__ not in _BENIGN:
            self.check.note(CHANNEL, f"The feed had XML errors ({_why(error)}); entries still read")
        feed_title = feed.feed.get("title")
        for entry in feed.entries:
            yield self.item(entry, feed_title)

    def item(self, entry: Any, feed_title: str | None) -> ListingItem:
        guid = entry.get("id") or entry.get("guid")
        link = entry.get("link") or (guid if str(guid or "").startswith("http") else None)
        description = _description(entry)
        return ListingItem(
            source_id=self.source.id,
            run_id=self.run_ref,
            channel=CHANNEL.value,
            title=entry.get("title") or "",
            url=link or "",
            ref_no=None,  # the NormalisePipeline reads it from the title if there is one
            displayed_date=entry.get("published") or entry.get("updated"),
            raw_extra={
                "guid": guid,
                "description_html": description,  # document text for this notice
                "pub_date_raw": entry.get("published") or entry.get("updated"),
                "feed_title": feed_title,
            },
        )


def _why(error: Exception) -> str:
    return f"{type(error).__name__}: {str(error).strip() or 'no detail'}"[:300]


def _description(entry: Any) -> str | None:
    """The longest of the entry's content blocks and summary (the fullest text the feed gives)."""
    candidates = [c.get("value") for c in entry.get("content") or []]
    candidates.append(entry.get("summary"))
    candidates = [c for c in candidates if c]
    return max(candidates, key=len) if candidates else None
