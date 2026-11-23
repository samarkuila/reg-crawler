"""Helpers that build crawl runs with raw listing rows, so detection can be tested in sequences
(run 1 -> run 2 -> ...) on either repository. No network, no Scrapy: the rows are what a crawl
would have stored."""

import uuid
from datetime import date

from app.schemas.entities import RawListingItem, SourceCheck, SourceVersion, now_ist
from app.schemas.enums import (
    CheckChannel,
    CheckStatus,
    Cycle,
    SourceType,
    VersionStatus,
)

RSS, HTML, XML = CheckChannel.RSS, CheckChannel.HTML, CheckChannel.XML


def make_source(repo, source_id: str = "det-both", **updates):
    """An active source with its own history (the demo seed's sources already have some)."""
    template = repo.get_source("rbi")
    fields = {
        "id": source_id,
        "page_name": "Detection test source",
        "type": SourceType.HTML_LIST,
        "rss_url": "https://example.test/feed.xml",
        "url": "https://example.test/list",
        "include_keywords": [],
        "exclude_keywords": [],
        "current_version": 1,
        "active": True,
        **updates,
    }
    source = template.model_copy(update=fields)
    version = SourceVersion(
        id=f"sv-{source_id}",
        source_id=source_id,
        version=1,
        after=source.model_dump(mode="json"),
        status=VersionStatus.APPROVED,
        requested_by="tester",
        approved_by="approver",
        ts=now_ist(),
    )
    repo.create_source(source, version, "tester")
    return source


def row(
    title: str,
    ref_no: str | None = None,
    when: date | None = None,
    url: str | None = None,
    **extra,
) -> dict:
    slug = (ref_no or title).lower().replace(" ", "-").replace("/", "-")
    return {
        "title": title,
        "ref_no": ref_no,
        "displayed_date": when,
        "url": url or f"https://example.test/n/{slug}",
        "raw_extra": extra,
    }


def crawl(
    repo,
    source_id: str,
    on: date,
    rss: list[dict] | None = None,
    html: list[dict] | None = None,
    *,
    rss_status: CheckStatus | None = None,
    html_status: CheckStatus | None = None,
    cycle: Cycle = Cycle.ADHOC,
    run=None,
):
    """One crawl run for `source_id`. A channel is read only if its rows (or a status) are given.

    `rss=[]` is a healthy empty feed (ZERO_ITEMS); `rss=None` with no status means not crawled.
    """
    run = run or repo.create_run(cycle, on, "tester", "tester")
    for channel, rows, status in ((RSS, rss, rss_status), (HTML, html, html_status)):
        if rows is None and status is None:
            continue
        rows = rows or []
        status = status or (CheckStatus.OK if rows else CheckStatus.ZERO_ITEMS)
        healthy = status in (CheckStatus.OK, CheckStatus.ZERO_ITEMS)
        stored = [
            RawListingItem(
                id=f"raw-{uuid.uuid4().hex[:10]}",
                run_id=run.id,
                source_id=source_id,
                channel=channel,
                snapshot_id=None,
                fetched_at=now_ist(),
                **r,
            )
            for r in rows
        ]
        repo.add_raw_items(stored, "crawler")
        repo.record_source_check(
            SourceCheck(
                id=f"chk-{run.id}-{source_id}-{channel.value.lower()}",
                run_id=run.id,
                source_id=source_id,
                channel=channel,
                status=status,
                items_found=len(stored) if healthy else None,
            ),
            "crawler",
        )
    return run


def crawl_xml(repo, source_id: str, on: date, rows: list[dict], *, run=None):
    run = run or repo.create_run(Cycle.ADHOC, on, "tester", "tester")
    stored = [
        RawListingItem(
            id=f"raw-{uuid.uuid4().hex[:10]}",
            run_id=run.id,
            source_id=source_id,
            channel=XML,
            snapshot_id=None,
            fetched_at=now_ist(),
            **r,
        )
        for r in rows
    ]
    repo.add_raw_items(stored, "crawler")
    repo.record_source_check(
        SourceCheck(
            id=f"chk-{run.id}-{source_id}-xml",
            run_id=run.id,
            source_id=source_id,
            channel=XML,
            status=CheckStatus.OK,
            items_found=len(stored),
        ),
        "crawler",
    )
    return run
