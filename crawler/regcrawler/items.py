"""Scrapy items. Spiders emit these; they never decide what is new (CLAUDE.md rule 6)."""

import scrapy


class ListingItem(scrapy.Item):
    """One row read from a feed, listing page or data file."""

    source_id = scrapy.Field()
    run_id = scrapy.Field()
    channel = scrapy.Field()  # "RSS" | "HTML" | "XML"
    title = scrapy.Field()
    url = scrapy.Field()
    ref_no = scrapy.Field()
    displayed_date = scrapy.Field()  # as read; the NormalisePipeline turns it into a date
    description_text = scrapy.Field()  # plain text; the DbPipeline derives it from description_html
    raw_extra = scrapy.Field()  # dict: description HTML, guid, anything else worth keeping
    snapshot_id = scrapy.Field()  # filled by SnapshotPipeline


class SnapshotItem(scrapy.Item):
    """The raw bytes of one fetched page. Consumed by SnapshotPipeline, which drops the bytes."""

    source_id = scrapy.Field()
    run_id = scrapy.Field()
    channel = scrapy.Field()
    url = scrapy.Field()
    body = scrapy.Field()
    http_status = scrapy.Field()
    snapshot_id = scrapy.Field()
