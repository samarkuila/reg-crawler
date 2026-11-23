"""Listing spider: reads an HTML listing page, driven entirely by the source's adapter_config.

    scrapy crawl listing -a source=<id> -a run_id=<id> [-a fixture=<saved page>]

adapter_config (edited on the Sources page, versioned and approved like any source change):

    selectors:
      row:    CSS selector of one listing row (required)
      title:  CSS selector inside the row (required); text of the element
      link:   CSS selector inside the row; the href is taken (default: the first <a>)
      date:   CSS selector inside the row (optional); the text is the date as shown
      ref_no: CSS selector inside the row (optional); else ref_no_regex is tried on the title
      ministry: CSS selector inside the row (only for sources with a ministry filter)
      next:   CSS selector of the "next page" link (optional; needed when max_pages > 1)
      ref_no_regex: regex for the reference number in the title
    date_format: strptime format of the displayed date (HTML pages only)
    max_pages: how many pages to read (pagination depth = lookback), default 1
    ministries: keep only rows whose ministry contains one of these (case-insensitive)
    always_keep: ministry texts that are never filtered out (for example "multiple ministries")
    use_search_form: must be false; this spider never submits a form

Rules this spider follows:
- It reads what the page shows. It never decides what is new (that is detection, rule 6).
- Keywords are NOT used to drop rows (rule 3). Matches are recorded as hints in raw_extra and
  detection ranks by them. Only the ministry filter leaves rows out, and the check says how many.
- Rows without a ministry are kept when the filter cannot be applied (never silently dropped).
- requires_js sources use scrapy-playwright if it is installed and enabled; there are no stealth
  options. If it is not available the check says so (FAILED) and nothing is fetched.
"""

import re
from collections.abc import Iterator
from typing import Any

from scrapy.http import Response

from app.schemas.enums import CheckChannel
from regcrawler.items import ListingItem
from regcrawler.spiders.base import BaseRegSpider

CHANNEL = CheckChannel.HTML
_PLACEHOLDER = re.compile(r"^\s*(TODO|TBD|CHANGE-?ME)", re.I)
_SPACE = re.compile(r"\s+")


class ListingConfigError(ValueError):
    """The source's reading rules are missing or unusable."""


def _clean(value: str | None) -> str:
    return _SPACE.sub(" ", (value or "").replace("\xa0", " ")).strip()


class ListingConfig:
    """The reading rules of one source, validated once."""

    def __init__(self, adapter_config: dict[str, Any]) -> None:
        sel = adapter_config.get("selectors") or {}
        self.row = str(sel.get("row") or "").strip()
        self.title = str(sel.get("title") or "").strip()
        self.link = str(sel.get("link") or "").strip()
        self.date = str(sel.get("date") or "").strip()
        self.ref_no = str(sel.get("ref_no") or "").strip()
        self.ministry = str(sel.get("ministry") or "").strip()
        self.next = str(sel.get("next") or "").strip()
        self.max_pages = max(1, int(adapter_config.get("max_pages") or 1))
        self.ministries = [m.casefold() for m in adapter_config.get("ministries") or []]
        self.always_keep = [m.casefold() for m in adapter_config.get("always_keep") or []]
        self.use_search_form = bool(adapter_config.get("use_search_form", False))
        for name in ("row", "title"):
            value = getattr(self, name)
            if not value:
                raise ListingConfigError(f"The {name} selector is not set")
            if _PLACEHOLDER.match(value):
                raise ListingConfigError(
                    f"The {name} selector is still a placeholder ({value}); "
                    "set it on the Sources page"
                )
        if self.use_search_form:
            raise ListingConfigError(
                "Search forms are never used; read the listing page instead (use_search_form)"
            )

    def keeps(self, ministry: str | None) -> bool:
        """Ministry filter. True when there is no filter, no way to tell, or it matches."""
        if not self.ministries or not ministry:
            return True
        text = ministry.casefold()
        return any(m in text for m in (*self.ministries, *self.always_keep))


class ListingSpider(BaseRegSpider):
    name = "listing"
    channels = (CHANNEL,)

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.config: ListingConfig | None = None
        self.config_error: str | None = None
        try:
            self.config = ListingConfig(self.source.adapter_config)
        except (ListingConfigError, ValueError, TypeError) as exc:
            self.config_error = str(exc)
        self.pages_read = 0
        self.out_of_scope = 0
        self.rows_seen = 0

    # --- requests ------------------------------------------------------------------------------

    @property
    def page_limit(self) -> int:
        if self.dry_run or self.fixture is not None or self.config is None:
            return 1  # a test reads one page; offline there is only one saved page
        return self.config.max_pages

    def page_request(self, url: str) -> Any:
        meta: dict[str, Any] = {}
        if self.source.requires_js:
            meta["playwright"] = True  # plain page render: no stealth, no evasion options
        return self.get(self.target(url), CHANNEL, self.parse, **meta)

    async def start(self) -> Any:
        if self.config is None:
            self.check.parse_error(CHANNEL, f"Reading rules unusable: {self.config_error}")
            return
        if self.source.requires_js and not self._playwright_ready():
            self.check.fail(
                CHANNEL,
                "This source needs a browser (requires_js) but scrapy-playwright is not "
                "installed or enabled; nothing was fetched",
            )
            return
        yield self.page_request(self.source.url)

    @staticmethod
    def _playwright_ready() -> bool:
        try:
            import scrapy_playwright  # noqa: F401
        except ImportError:
            return False
        from regcrawler import settings

        return bool(getattr(settings, "PLAYWRIGHT_ENABLED", False))

    # --- parsing -------------------------------------------------------------------------------

    def parse(self, response: Response) -> Iterator[Any]:
        assert self.config is not None
        yield self.snapshot_of(response, CHANNEL)  # the page as received, before anything else
        self.pages_read += 1
        try:
            rows = response.css(self.config.row)
        except Exception as exc:  # an invalid selector is a configuration problem
            self.check.parse_error(CHANNEL, f"Row selector is not valid: {exc}"[:300])
            return
        if not rows:
            if self.pages_read == 1:
                self.check.note(
                    CHANNEL,
                    "The row selector matched nothing on the page; has the page layout changed?",
                )
        for row in rows:
            self.rows_seen += 1
            item = self.row_item(row, response)
            if item is not None:
                yield item
        next_request = self.next_page(response)
        if next_request is not None:
            yield next_request
        else:
            self.finish_notes()

    def row_item(self, row: Any, response: Response) -> ListingItem | None:
        cfg = self.config
        assert cfg is not None
        title = self.text(row, cfg.title)
        ministry = self.text(row, cfg.ministry) if cfg.ministry else None
        if not cfg.keeps(ministry):
            self.out_of_scope += 1
            return None
        href = self.attr(row, cfg.link) if cfg.link else row.css("a::attr(href)").get()
        extra: dict[str, Any] = {}
        if ministry:
            extra["ministry"] = ministry
        text = title.casefold()
        include = [k for k in self.source.include_keywords if k.casefold() in text]
        exclude = [k for k in self.source.exclude_keywords if k.casefold() in text]
        if include:
            extra["keywords_matched"] = include
        if exclude:
            extra["exclude_hints"] = exclude  # a hint for ranking; the row is kept (rule 3)
        return ListingItem(
            source_id=self.source.id,
            run_id=self.run_ref,
            channel=CHANNEL.value,
            title=title,
            url=response.urljoin(href) if href else "",
            ref_no=self.text(row, cfg.ref_no) if cfg.ref_no else None,
            displayed_date=self.text(row, cfg.date) if cfg.date else None,
            raw_extra=extra,
        )

    def next_page(self, response: Response) -> Any:
        cfg = self.config
        assert cfg is not None
        if self.pages_read >= self.page_limit:
            return None
        if not cfg.next:
            self.check.note(
                CHANNEL, f"max_pages is {cfg.max_pages} but no 'next' selector is set; read 1 page"
            )
            return None
        href = self.attr(response, cfg.next)
        return self.page_request(response.urljoin(href)) if href else None

    def finish_notes(self) -> None:
        if self.out_of_scope:
            self.check.note(
                CHANNEL,
                f"{self.out_of_scope} of {self.rows_seen} row(s) were outside the ministry "
                "filter and not kept",
            )
        cfg = self.config
        if cfg and cfg.ministries and not cfg.ministry and self.rows_seen:
            self.check.note(
                CHANNEL, "ministries are set but no ministry selector; nothing filtered"
            )

    # --- selector helpers ----------------------------------------------------------------------

    @staticmethod
    def text(row: Any, selector: str) -> str:
        """Text of the first match (all text inside the element, whitespace collapsed)."""
        if not selector:
            return ""
        if "::" in selector:
            return _clean(" ".join(row.css(selector).getall()))
        found = row.css(selector)
        return _clean(found[0].xpath("string()").get()) if found else ""

    @staticmethod
    def attr(row: Any, selector: str) -> str | None:
        """href of the first match; the selector may or may not end in ::attr(...)."""
        css = selector if "::attr(" in selector else f"{selector}::attr(href)"
        return row.css(css).get()
