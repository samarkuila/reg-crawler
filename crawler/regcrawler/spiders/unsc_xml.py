"""UNSC spider: downloads the consolidated sanctions list and reports what changed.

    scrapy crawl unsc_xml -a source=<source id> -a run_id=<run id> [-a fixture=<saved XML file>]

The XML is stored as a snapshot, then compared with the previous healthy snapshot of this source.
Every ADDED / REMOVED / AMENDED entry becomes a ListingItem (channel XML, priority URGENT) with the
reference number, names and aliases. Nothing is decided here: a person reviews every item.

First run: there is nothing to compare with, so the file is stored as the baseline and no items are
emitted (otherwise every entry on the list would look new). The check says so.
"""

from collections.abc import Iterator
from typing import Any

from scrapy.http import Response

from app.schemas.enums import CheckChannel
from regcrawler import unsc
from regcrawler.items import ListingItem
from regcrawler.spiders.base import BaseRegSpider

CHANNEL = CheckChannel.XML


class UnscXmlSpider(BaseRegSpider):
    name = "unsc_xml"
    channels = (CHANNEL,)

    async def start(self) -> Any:
        yield self.get(self.target(self.source.url), CHANNEL, self.parse)

    def parse(self, response: Response) -> Iterator[Any]:
        yield self.snapshot_of(response, CHANNEL)  # always keep the file we were given
        state = self.check.state(CHANNEL)
        state.empty_is_ok = True  # "nothing changed" is the normal answer for a diff source
        try:
            new = unsc.parse_consolidated(response.body)
        except unsc.UnscError as exc:
            self.check.parse_error(CHANNEL, str(exc)[:300])
            return
        if not new.entries:
            # An empty list would turn every entry into REMOVED. Treat it as a broken download.
            self.check.parse_error(CHANNEL, "The consolidated list has no entries")
            return

        previous = self.previous_snapshot(CHANNEL)
        if previous is None:
            self.check.note(
                CHANNEL,
                f"Baseline stored ({len(new.entries)} entries); nothing to compare with yet",
            )
            return
        snapshot, content = previous
        try:
            old = unsc.parse_consolidated(content)
        except unsc.UnscError as exc:
            self.check.note(
                CHANNEL, f"Previous snapshot unreadable ({exc}); stored as new baseline"
            )
            return
        if new.skipped:
            self.check.note(CHANNEL, f"{new.skipped} entry(ies) without a reference number skipped")
        for change in unsc.diff(old, new):
            yield self.item(change, snapshot.id)

    def item(self, change: unsc.Difference, previous_snapshot_id: str) -> ListingItem:
        kind = "individual" if change.kind == "individual" else "entity"
        return ListingItem(
            source_id=self.source.id,
            run_id=self.run_ref,
            channel=CHANNEL.value,
            title=f"UNSC sanctions list {change.change.value.lower()}: {change.name} "
            f"({change.ref}) - {change.summary}",
            # The list is one file; the reference makes the address of this entry unique.
            url=f"{self.source.url}?reference={change.ref}",
            ref_no=change.ref,
            displayed_date=change.listed_on if change.change == unsc.Change.ADDED else None,
            raw_extra={
                "priority": "URGENT",
                "change": change.change.value,
                "summary": change.summary,
                "kind": kind,
                "name": change.name,
                "name_before": change.name_before,
                "aliases": list(change.aliases),
                "aliases_added": list(change.aliases_added),
                "aliases_removed": list(change.aliases_removed),
                "changed_details": list(change.changed_details),
                "compared_with_snapshot_id": previous_snapshot_id,
            },
        )
