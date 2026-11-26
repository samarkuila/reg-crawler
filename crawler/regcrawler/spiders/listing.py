"""Listing spider: reads an HTML listing page, driven entirely by the source's adapter_config.

    scrapy crawl listing -a source=<id> -a run_id=<id> [-a lookback_days=N]
        [-a fixture=<saved page>]

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
    date_from_header_row: true when dates and section names are heading rows between the item
      rows instead of a column (the RBI Master Directions page). Needs selectors.header_row (the
      heading rows) and date_format. A heading row that parses with date_format is a date; any
      other heading row is a section label. Each item row gets the latest date above it (unless
      the row shows its own date) and the latest section label as raw_extra["category"]. The
      latest date is simply the nearest one above: dates repeat and are only in order inside a
      section, so nothing is sorted or assumed about the whole page.
    max_pages: the most pages ever read, default 1. Paging also stops early, before this cap, once
      a whole page is dated before the lookback window (-a lookback_days, default 7). Rows on the
      page that was read are all kept; a page with no readable dates does not stop paging.
    ministries: keep only rows whose ministry contains one of these (case-insensitive)
    always_keep: ministry texts that are never filtered out (for example "multiple ministries")
    use_search_form: must be false; this spider never submits a form

Rules this spider follows:
- It reads what the page shows. It never decides what is new (that is detection, rule 6).
- Keywords are NOT used to drop rows (rule 3). Matches are recorded as hints in raw_extra and
  detection ranks by them. Only the ministry filter leaves rows out, and the check says how many.
- Rows without a ministry are kept when the filter cannot be applied (never silently dropped).
- fetch_engine = browser sources are rendered by scrapy-playwright (plain headless Chromium, no
  stealth options), then parsed by exactly the same code as a standard page. The browser waits for
  `wait_for_selector` (else the row selector) up to `wait_timeout_s`; if that element never
  appears the check is Zero items with a hint, not Failed. If scrapy-playwright or Chromium is
  missing the check is FAILED, says to run `.\\tasks.ps1 install-browser`, and nothing is fetched.
  Block detection runs on the rendered page too. The engine is chosen by a person, never switched
  automatically.
"""

import re
from collections.abc import Iterator
from datetime import date, datetime
from typing import Any

from scrapy.http import Response

from app.schemas.enums import CheckChannel, FetchEngine
from regcrawler.browser import WAIT_MISSING_HINT, unavailable_reason, wait_for_list
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
        self.attachments = str(sel.get("attachments") or "").strip()  # links inside the row
        self.header_row = str(sel.get("header_row") or "").strip()
        self.date_from_header_row = bool(adapter_config.get("date_from_header_row", False))
        self.date_format = str(adapter_config.get("date_format") or "").strip()
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
        if self.date_from_header_row:
            if not self.header_row:
                raise ListingConfigError(
                    "date_from_header_row needs the header_row selector (the heading rows "
                    "that hold dates and section names)"
                )
            if _PLACEHOLDER.match(self.header_row):
                raise ListingConfigError(
                    f"The header_row selector is still a placeholder ({self.header_row}); "
                    "set it on the Sources page"
                )
            if not self.date_format:
                raise ListingConfigError(
                    "date_from_header_row needs date_format to tell a date from a section name"
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
        # Heading-row state (date_from_header_row). It carries across pages: a section can run on.
        self.header_date: str | None = None  # the latest date heading, as shown on the page
        self.category: str | None = None  # the latest section label
        self.undated_rows = 0
        self.stopped_by_window = False

    # --- requests ------------------------------------------------------------------------------

    @property
    def page_limit(self) -> int:
        if self.dry_run or self.fixture is not None or self.config is None:
            return 1  # a test reads one page; offline there is only one saved page
        return min(self.config.max_pages, self.request_cap)

    @property
    def engine_used(self) -> str:
        return FetchEngine.BROWSER.value if self.source.uses_browser else FetchEngine.HTTP.value

    def page_request(self, url: str) -> Any:
        meta: dict[str, Any] = {}
        if self.source.uses_browser and self.config is not None:
            from scrapy_playwright.page import PageMethod

            # Plain page render: no stealth, no evasion options. Wait for the list, never raise.
            selector = self.source.wait_for_selector or self.config.row
            outcome: dict[str, Any] = {}
            meta["playwright"] = True
            meta["playwright_page_methods"] = [
                PageMethod(wait_for_list, selector, self.source.wait_timeout_s * 1000, outcome)
            ]
            meta["wait_outcome"] = outcome
        return self.get(self.target(url), CHANNEL, self.parse, **meta)

    async def start(self) -> Any:
        if self.config is None:
            self.check.parse_error(CHANNEL, f"Reading rules unusable: {self.config_error}")
            return
        if self.source.uses_browser:
            problem = unavailable_reason()
            if problem is None and not self._playwright_ready():
                problem = (
                    "This source is fetched with the browser, but this crawl was not started "
                    "with the browser engine on (the crawl runner and Test this source do that)"
                )
            if problem is not None:
                self.check.fail(CHANNEL, f"{problem}; nothing was fetched")
                return
        yield self.page_request(self.source.url)

    @staticmethod
    def _playwright_ready() -> bool:
        from regcrawler import settings

        return bool(getattr(settings, "PLAYWRIGHT_ENABLED", False))

    def closed(self, reason: str) -> None:
        """One log line per crawl: engine, browser render time, requests made."""
        self.logger.info(
            "fetch engine=%s render_time=%.1fs requests=%d pages=%d (%s)",
            self.engine_used,
            self.render_seconds,
            self.requests_made,
            self.pages_read,
            reason,
        )

    # --- parsing -------------------------------------------------------------------------------

    def parse(self, response: Response) -> Iterator[Any]:
        assert self.config is not None
        yield self.snapshot_of(response, CHANNEL)  # the page as received, before anything else
        self.pages_read += 1
        if self.source.uses_browser:  # the handler records how long the render took
            self.render_seconds += float(response.meta.get("download_latency") or 0.0)
        waited = response.meta.get("wait_outcome")
        wait_missed = waited is not None and waited.get("found") is False
        try:
            entries = self.page_entries(response)
        except Exception as exc:  # an invalid selector is a configuration problem
            self.check.parse_error(CHANNEL, f"Row selector is not valid: {exc}"[:300])
            return
        if not any(kind == "row" for kind, _ in entries):
            if self.pages_read == 1:
                self.check.note(
                    CHANNEL,
                    WAIT_MISSING_HINT
                    if wait_missed
                    else "The row selector matched nothing on the page; has the page layout "
                    "changed?",
                )
        page_dates: list[date] = []
        for kind, row in entries:
            if kind == "heading":
                self.read_heading(row)
                continue
            self.rows_seen += 1
            item = self.row_item(row, response, page_dates)
            if item is not None:
                yield item
        next_request = self.next_page(response, page_dates)
        if next_request is not None:
            yield next_request
        else:
            self.finish_notes()

    def page_entries(self, response: Response) -> list[tuple[str, Any]]:
        """("row" | "heading", selector) in page order. Headings only with date_from_header_row."""
        cfg = self.config
        assert cfg is not None
        if not cfg.date_from_header_row:
            return [("row", row) for row in response.css(cfg.row)]
        headings = {h.root for h in response.css(cfg.header_row)}
        # One combined query keeps document order. A row that is also a heading is a heading.
        return [
            ("heading" if s.root in headings else "row", s)
            for s in response.css(f"{cfg.header_row}, {cfg.row}")
        ]

    def read_heading(self, heading: Any) -> None:
        """A heading that parses with date_format is a date; any other heading is a section."""
        cfg = self.config
        assert cfg is not None
        text = _clean(heading.xpath("string()").get())
        if not text:
            return
        try:
            datetime.strptime(text, cfg.date_format)
        except ValueError:
            self.category = text
        else:
            self.header_date = text

    def parse_shown_date(self, text: str | None) -> date | None:
        """The displayed date as a day, or None when it cannot be read (then it proves nothing)."""
        cfg = self.config
        text = _clean(text)
        if not text or cfg is None:
            return None
        formats = [cfg.date_format] if cfg.date_format else []
        for fmt in (*formats, "%Y-%m-%d"):
            try:
                return datetime.strptime(text, fmt).date()
            except ValueError:
                continue
        return None

    def row_item(
        self, row: Any, response: Response, page_dates: list[date] | None = None
    ) -> ListingItem | None:
        cfg = self.config
        assert cfg is not None
        title = self.text(row, cfg.title)
        ministry = self.text(row, cfg.ministry) if cfg.ministry else None
        if page_dates is not None:  # window check sees every row, including filtered ones
            shown = self.text(row, cfg.date) if cfg.date else ""
            if cfg.date_from_header_row:
                shown = shown or self.header_date or ""
            day = self.parse_shown_date(shown)
            if day is not None:
                page_dates.append(day)
        if not cfg.keeps(ministry):
            self.out_of_scope += 1
            return None
        href = self.attr(row, cfg.link) if cfg.link else row.css("a::attr(href)").get()
        extra: dict[str, Any] = {}
        if ministry:
            extra["ministry"] = ministry
        if cfg.attachments:
            links = [
                response.urljoin(h) for h in row.css(f"{cfg.attachments}::attr(href)").getall()
            ]
            if links:
                extra["attachments"] = sorted(dict.fromkeys(links))
        text = title.casefold()
        include = [k for k in self.source.include_keywords if k.casefold() in text]
        exclude = [k for k in self.source.exclude_keywords if k.casefold() in text]
        if include:
            extra["keywords_matched"] = include
        if exclude:
            extra["exclude_hints"] = exclude  # a hint for ranking; the row is kept (rule 3)
        displayed_date = self.text(row, cfg.date) if cfg.date else None
        if cfg.date_from_header_row:
            displayed_date = displayed_date or self.header_date  # a date the row shows wins
            if not displayed_date:
                self.undated_rows += 1
            if self.category:
                extra["category"] = self.category
        return ListingItem(
            source_id=self.source.id,
            run_id=self.run_ref,
            channel=CHANNEL.value,
            title=title,
            url=response.urljoin(href) if href else "",
            ref_no=self.text(row, cfg.ref_no) if cfg.ref_no else None,
            displayed_date=displayed_date,
            raw_extra=extra,
        )

    def next_page(self, response: Response, page_dates: list[date] | None = None) -> Any:
        cfg = self.config
        assert cfg is not None
        if self.pages_read >= self.page_limit:
            if self.pages_read >= self.request_cap < self.config.max_pages:
                self.check.note(
                    CHANNEL,
                    f"Stopped paging after {self.requests_made} requests: the limit of "
                    f"{self.request_cap} page requests to this website in one run",
                )
            return None
        window = self.window_start
        if (
            self.source.sorted_newest_first  # the page lists newest first, so older rows follow
            and window is not None
            and page_dates
            and max(page_dates) < window
        ):
            # Every dated row on this page is older than the window: do not page further back.
            self.stopped_by_window = True
            what = (
                f"the first-run window (from {window.isoformat()})"
                if self.first_run
                else f"the {self.lookback_days}-day lookback window"
            )
            self.check.note(
                CHANNEL,
                f"Stopped paging after page {self.pages_read}: "
                f"all dated rows are older than {what}",
            )
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
        if self.undated_rows:
            self.check.note(
                CHANNEL, f"{self.undated_rows} row(s) came before any date heading and have no date"
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
