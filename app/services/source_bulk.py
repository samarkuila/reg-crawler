"""Bulk add of sources from a CSV / Excel file or pasted rows (Sources page, "Bulk add").

Flow: preview (checks every row, writes NOTHING) -> save (re-checks, then creates one PENDING
version per OK/Warning row in one transaction) -> a different approver decides (maker–checker,
CLAUDE.md rule 9). The server re-checks on save, so a preview is never trusted.

Also here: the politeness gate for "Test selected" (one request per host at a time, at least 2 s
between requests to a host, and a circuit breaker). It never works around a verification page.
"""

import csv
import io
import re
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml
from parsel import Selector

from app.repositories.base import Repository
from app.schemas.api import (
    BulkDecisionItem,
    BulkDecisionResult,
    BulkPreview,
    BulkRowResult,
    BulkSaveResult,
    SourceTestResult,
)
from app.schemas.entities import AppUser, Source, SourceVersion, now_ist
from app.schemas.enums import (
    CheckStatus,
    FetchEngine,
    FirstRunMode,
    Priority,
    SourceType,
    VersionStatus,
)
from app.services.errors import DomainError, InvalidError
from app.services.permissions import CONFIG_ROLES, require_role

MAX_ROWS = 50
MAX_BYTES = 1_000_000
ROOT = Path(__file__).resolve().parents[2]

COLUMNS = (
    "authority",
    "page_name",
    "url",
    "rss_url",
    "read_mode",
    "row_selector",
    "title_selector",
    "link_selector",
    "date_selector",
    "heading_rows_selector",
    "headings_are_rows",
    "ref_no_regex",
    "date_format",
    "include_keywords",
    "exclude_keywords",
    "priority",
)
# Optional columns: the first-crawl window and how the page is fetched. A file without them (the
# 16 columns above) imports unchanged.
OPTIONAL_COLUMNS = (
    "first_run_mode",
    "first_run_days",
    "sorted_newest_first",
    "fetch_engine",
    "wait_for_selector",
    "wait_timeout_s",
)
ALLOWED_COLUMNS = (*COLUMNS, *OPTIONAL_COLUMNS)
REQUIRED = ("authority", "page_name", "url", "read_mode")
READ_MODES = {
    "rss_html": SourceType.RSS,
    "html": SourceType.HTML_LIST,
    "data_file": SourceType.STRUCTURED_DATA,
}
OLD_BROWSER_READ_MODE = "browser"  # accepted from old files: read as html + fetch_engine browser
FETCH_ENGINES = {m.value for m in FetchEngine}
# The system has two priorities (Normal, Urgent). "High" is accepted as Urgent; "Low" is not
# something the system can do, so it is refused with a clear message rather than guessed.
PRIORITIES = {"normal": Priority.NORMAL, "urgent": Priority.URGENT, "high": Priority.URGENT}
TEMPLATE_ROWS = (
    (
        "RBI", "Notifications", "https://www.rbi.org.in/Scripts/NotificationUser.aspx",
        "https://rbi.org.in/notifications_rss.xml", "rss_html", "table.tablebg tr",
        "a.link2", "a.link2", "td.date", "", "false", r"RBI/\d{4}-\d{2}/\d+", "%b %d, %Y",
        "NBFC|KYC", "auction|recruitment", "Normal",
    ),
    (
        "Example authority", "Example page", "https://www.example.gov.in/circulars", "",
        "html", "div.list li", "a", "a", "span.date", "", "false", "", "%d.%m.%Y", "", "", "Normal",
    ),
)  # fmt: skip
_TOKEN = re.compile(r"\(S\([^)]*\)\)", re.IGNORECASE)
_TRUE = {"true", "yes", "y", "1"}
_FALSE = {"false", "no", "n", "0"}
_SEARCH_MARKERS = ("search", "query=", "q=")


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:60]


def _cell(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _safe_cell(value: str) -> str:
    """Stop a spreadsheet reading a cell of the errors file as a formula."""
    return f"'{value}" if value[:1] in ("=", "+", "-", "@") else value


def template_csv() -> str:
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(COLUMNS)
    writer.writerows(TEMPLATE_ROWS)
    return out.getvalue()


def _load_settings() -> dict[str, Any]:
    try:
        data = yaml.safe_load((ROOT / "config" / "settings.yaml").read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


# --- reading the upload ----------------------------------------------------------------------


def _rows_from_matrix(matrix: list[list[str]]) -> list[dict[str, str]]:
    matrix = [r for r in matrix if any(c.strip() for c in r)]
    if not matrix:
        raise InvalidError("The file is empty. Use the template: it has a header row.")
    header = [h.strip().lower().replace(" ", "_") for h in matrix[0]]
    unknown = [h for h in header if h and h not in ALLOWED_COLUMNS]
    if unknown:
        raise InvalidError(
            "Unknown column(s): "
            + ", ".join(unknown)
            + ". Allowed columns: "
            + ", ".join(ALLOWED_COLUMNS)
        )
    if len(set(header)) != len(header):
        raise InvalidError("A column name appears twice in the header row.")
    missing = [c for c in REQUIRED if c not in header]
    if missing:
        raise InvalidError("Missing required column(s): " + ", ".join(missing))
    body = matrix[1:]
    if not body:
        raise InvalidError("The file has a header row but no source rows.")
    if len(body) > MAX_ROWS:
        raise InvalidError(
            f"The file has {len(body)} rows. Upload at most {MAX_ROWS} at a time; split the file."
        )
    return [
        {c: (row[i].strip() if i < len(row) else "") for i, c in enumerate(header) if c}
        for row in body
    ]


def read_rows(file_name: str | None, data: bytes | None, text: str | None) -> list[dict[str, str]]:
    """Rows from an uploaded .csv/.xlsx file, or from pasted text. Raises InvalidError."""
    if data:
        if len(data) > MAX_BYTES:
            raise InvalidError("The file is larger than 1 MB.")
        name = (file_name or "").lower()
        if name.endswith(".xlsx"):
            return _rows_from_matrix(_xlsx_matrix(data))
        if not name.endswith(".csv"):
            raise InvalidError("Upload a .csv or .xlsx file, or paste the rows.")
        try:
            content = data.decode("utf-8-sig")
        except UnicodeDecodeError as exc:
            raise InvalidError("The file must be UTF-8 text. Save it as “CSV UTF-8”.") from exc
    elif text and text.strip():
        content = text.lstrip("﻿")
    else:
        raise InvalidError("Choose a file or paste rows first.")
    first = content.splitlines()[0] if content.strip() else ""
    delimiter = "\t" if "\t" in first and "," not in first else ","
    try:
        matrix = list(csv.reader(io.StringIO(content), delimiter=delimiter))
    except csv.Error as exc:
        raise InvalidError(f"The rows could not be read: {exc}") from exc
    return _rows_from_matrix(matrix)


def _xlsx_matrix(data: bytes) -> list[list[str]]:
    from openpyxl import load_workbook  # already a project dependency

    try:
        book = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        sheet = book.worksheets[0]
        return [[_cell(c) for c in row] for row in sheet.iter_rows(values_only=True)]
    except Exception as exc:  # noqa: BLE001 - any broken workbook is the same message
        raise InvalidError("The Excel file could not be read. Use the CSV template.") from exc


# --- checking rows ---------------------------------------------------------------------------


def _valid_date_format(fmt: str) -> bool:
    if "%" not in fmt:
        return False
    try:
        sample = datetime(2026, 11, 20).strftime(fmt)
        datetime.strptime(sample, fmt)
    except ValueError:
        return False
    return True


class _Checker:
    def __init__(self, existing: list[Source], settings: dict[str, Any]) -> None:
        live = [s for s in existing if not s.retired]
        self.known = {(s.url.strip().lower(), s.page_name.strip().lower()) for s in live}
        crawler = settings.get("crawler") or {}
        self.suffixes = [str(h).lower().lstrip(".") for h in crawler.get("allowed_hosts") or []]
        self.hosts = {(urlparse(s.url).hostname or "").lower() for s in live}
        self.login_markers = [str(m).lower() for m in crawler.get("login_url_markers") or []] or [
            "/login",
            "/signin",
            "/sso/",
            "/auth/",
        ]
        self.seen: set[tuple[str, str]] = set()

    def host_allowed(self, host: str) -> bool:
        if host in self.hosts:
            return True
        return any(host == s or host.endswith("." + s) for s in self.suffixes)

    def check(self, number: int, raw: dict[str, str]) -> BulkRowResult:  # noqa: PLR0912, PLR0915
        errors: list[str] = []
        warnings: list[str] = []
        get = lambda k: raw.get(k, "").strip()  # noqa: E731

        for key in REQUIRED:
            if not get(key):
                errors.append(f"{key} is required.")
        url = get("url")
        if _TOKEN.search(url):
            url = _TOKEN.sub("", url)
            warnings.append("A session token such as (S(xxxx)) was removed from the url.")
        if url and not re.match(r"^https?://[^\s/]+\S*$", url):
            errors.append("url must start with http:// or https://.")
            url = ""
        rss_url = get("rss_url")
        if _TOKEN.search(rss_url):
            rss_url = _TOKEN.sub("", rss_url)
            warnings.append("A session token was removed from the rss_url.")
        if rss_url and not re.match(r"^https?://[^\s/]+\S*$", rss_url):
            errors.append("rss_url must start with http:// or https://.")
            rss_url = ""

        mode = get("read_mode").lower()
        engine = get("fetch_engine").lower()
        if engine and engine not in FETCH_ENGINES:
            errors.append("fetch_engine must be http or browser.")
            engine = ""
        if mode == OLD_BROWSER_READ_MODE:
            if engine == FetchEngine.HTTP.value:
                errors.append("read_mode browser means fetch_engine browser, not http.")
            warnings.append(
                "read_mode browser is the old way of asking for a browser. It was read as "
                "read_mode html with fetch_engine browser."
            )
            mode, engine = "html", FetchEngine.BROWSER.value
        engine = engine or FetchEngine.HTTP.value
        source_type: SourceType | None = None
        if mode:
            source_type = READ_MODES.get(mode)
            if source_type is None:
                errors.append("read_mode must be one of: " + ", ".join(READ_MODES) + ".")
        if engine == FetchEngine.BROWSER.value and mode and mode != "html":
            errors.append(
                "fetch_engine browser is only for read_mode html (an RSS feed is always fetched "
                "with http, and a data file is not a page)."
            )
        wait: dict[str, Any] = {}
        if get("wait_for_selector"):
            try:
                Selector(text="<html></html>").css(get("wait_for_selector"))
                wait["wait_for_selector"] = get("wait_for_selector")
            except Exception:  # noqa: BLE001 - parsel raises several error types
                errors.append("wait_for_selector is not a valid CSS selector.")
        if get("wait_timeout_s"):
            try:
                seconds = int(get("wait_timeout_s"))
                if not 1 <= seconds <= 60:
                    raise ValueError
                wait["wait_timeout_s"] = seconds
            except ValueError:
                errors.append("wait_timeout_s must be a whole number from 1 to 60.")

        selectors = {
            "row": get("row_selector"),
            "title": get("title_selector"),
            "link": get("link_selector"),
            "date": get("date_selector"),
            "header_row": get("heading_rows_selector"),
            "ref_no_regex": get("ref_no_regex"),
        }
        date_format = get("date_format")
        if mode in ("html", "rss_html"):
            lacking = [
                label
                for key, label in (("row", "row_selector"), ("title", "title_selector"),
                                   ("link", "link_selector"))
                if not selectors[key]
            ]  # fmt: skip
            if lacking:
                errors.append(f"read_mode {mode} needs {', '.join(lacking)}.")
            if not date_format:
                errors.append(f"read_mode {mode} needs a date_format such as %d.%m.%Y.")
        if date_format and not _valid_date_format(date_format):
            errors.append("date_format is not a valid strptime format (for example %d.%m.%Y).")
        if selectors["ref_no_regex"]:
            try:
                re.compile(selectors["ref_no_regex"])
            except re.error as exc:
                errors.append(f"ref_no_regex is not a valid pattern: {exc}.")
        if mode == "rss_html" and not rss_url and url:
            warnings.append("read_mode rss_html usually has an rss_url; none was given.")

        flag = get("headings_are_rows").lower()
        headings = False
        if flag in _TRUE:
            headings = True
        elif flag and flag not in _FALSE:
            errors.append("headings_are_rows must be true or false.")
        if headings:
            if not selectors["header_row"]:
                errors.append("headings_are_rows is true, so heading_rows_selector is needed.")
            if not date_format:
                errors.append("headings_are_rows is true, so date_format is needed.")

        window: dict[str, Any] = {}
        if get("first_run_mode"):
            mode_word = get("first_run_mode").lower()
            if mode_word not in {m.value for m in FirstRunMode}:
                errors.append("first_run_mode must be current_month, last_n_days or all.")
            else:
                window["first_run_mode"] = mode_word
        if get("first_run_days"):
            try:
                days = int(get("first_run_days"))
                if not 1 <= days <= 3650:
                    raise ValueError
                window["first_run_days"] = days
            except ValueError:
                errors.append("first_run_days must be a whole number from 1 to 3650.")
        if get("sorted_newest_first"):
            word = get("sorted_newest_first").lower()
            if word in _TRUE or word in _FALSE:
                window["sorted_newest_first"] = word in _TRUE
            else:
                errors.append("sorted_newest_first must be true or false.")

        priority = Priority.NORMAL
        if get("priority"):
            word = get("priority").lower()
            if word == "low":
                errors.append("priority Low is not available here. Use Normal or Urgent.")
            elif word not in PRIORITIES:
                errors.append("priority must be Normal or Urgent (High is read as Urgent).")
            else:
                priority = PRIORITIES[word]
                if word == "high":
                    warnings.append("priority High was read as Urgent.")

        if url:
            host = (urlparse(url).hostname or "").lower()
            if not self.host_allowed(host):
                warnings.append(
                    f"{host} is not on the list of known regulator websites. Check the address."
                )
            lowered = urlparse(url)
            probe = f"{lowered.path}?{lowered.query}".lower()
            if any(m in probe for m in self.login_markers + list(_SEARCH_MARKERS)):
                warnings.append("The url looks like a login or search page. Use the listing page.")

        key = (url.lower(), get("page_name").lower())
        if url and get("page_name"):
            if key in self.known:
                errors.append("Duplicate: a source with this url and page_name already exists.")
            elif key in self.seen:
                errors.append("Duplicate: this url and page_name appear earlier in the file.")
            self.seen.add(key)

        fields: dict[str, Any] | None = None
        if not errors and source_type is not None:
            config: dict[str, Any] = {}
            kept = {k: v for k, v in selectors.items() if v}
            if kept:
                config["selectors"] = kept
            if date_format:
                config["date_format"] = date_format
            if headings:
                config["date_from_header_row"] = True
            fields = {
                "authority": get("authority"),
                "page_name": get("page_name"),
                "type": source_type.value,
                "url": url,
                "rss_url": rss_url or None,
                "adapter_config": config,
                "include_keywords": _keywords(get("include_keywords")),
                "exclude_keywords": _keywords(get("exclude_keywords")),
                "priority": priority.value,
                "fetch_engine": engine,
                **wait,
                **window,
            }
        status = "ERROR" if errors else ("WARNING" if warnings else "OK")
        return BulkRowResult(
            row=number,
            status=status,
            reasons=errors + warnings,
            raw=raw,
            fields=fields,
            authority=get("authority"),
            page_name=get("page_name"),
            url=url or get("url"),
        )


def _keywords(text: str) -> list[str]:
    return list(dict.fromkeys(k.strip() for k in text.split("|") if k.strip()))


# --- the service -----------------------------------------------------------------------------


class SourceBulkService:
    def __init__(self, repo: Repository, settings: dict[str, Any] | None = None) -> None:
        self._repo = repo
        self._settings = settings

    def _check(self, rows: list[dict[str, str]]) -> list[BulkRowResult]:
        checker = _Checker(self._repo.list_sources(), self._settings or _load_settings())
        return [checker.check(i, r) for i, r in enumerate(rows, start=1)]

    def preview(
        self, user: AppUser, file_name: str | None, data: bytes | None, text: str | None
    ) -> BulkPreview:
        """Check every row. Nothing is written."""
        require_role(user, CONFIG_ROLES, "bulk add sources")
        results = self._check(read_rows(file_name, data, text))
        return BulkPreview(
            file_name=file_name or "pasted rows",
            rows=results,
            ok=sum(r.status == "OK" for r in results),
            warnings=sum(r.status == "WARNING" for r in results),
            errors=sum(r.status == "ERROR" for r in results),
        )

    def save(self, user: AppUser, file_name: str, rows: list[dict[str, str]]) -> BulkSaveResult:
        require_role(user, CONFIG_ROLES, "bulk add sources")
        if not rows:
            raise InvalidError("There are no rows to save.")
        if len(rows) > MAX_ROWS:
            raise InvalidError(f"Upload at most {MAX_ROWS} rows at a time.")
        cleaned = [{k: _cell(v) for k, v in r.items() if k in ALLOWED_COLUMNS} for r in rows]
        results = self._check(cleaned)
        valid = [r for r in results if r.fields is not None]
        if not valid:
            raise InvalidError("No row is valid, so nothing was saved.")
        taken = {s.id for s in self._repo.list_sources()}
        entries: list[tuple[Source, SourceVersion]] = []
        for res in valid:
            fields = res.fields or {}
            base = _slug(f"{fields['authority']}-{fields['page_name']}")
            if not re.match(r"^[a-z0-9][a-z0-9-]{1,60}$", base):
                res.status, res.fields = "ERROR", None
                res.reasons.append("Could not make an id from the authority and page name.")
                continue
            source_id, number = base, 2
            while source_id in taken:
                source_id, number = f"{base}-{number}", number + 1
            taken.add(source_id)
            res.source_id = source_id
            source = Source(
                id=source_id,
                active=False,
                current_version=0,
                **{**fields, "type": SourceType(fields["type"]), "priority": fields["priority"]},
            )
            version = SourceVersion(
                id=f"srcv-{source_id}-1",
                source_id=source_id,
                version=1,
                before={},
                after={**fields, "active": True},
                status=VersionStatus.PENDING,
                requested_by=user.name,
                ts=now_ist(),
            )
            entries.append((source, version))
        if not entries:
            raise InvalidError("No row is valid, so nothing was saved.")
        batch = {
            "batch_id": f"imp-{uuid.uuid4().hex[:10]}",
            "file_name": file_name,
            "uploaded_by": user.name,
            "rows_in_file": len(results),
            "rows_ok": sum(r.status == "OK" for r in results),
            "rows_warning": sum(r.status == "WARNING" for r in results),
            "rows_error": sum(r.status == "ERROR" for r in results),
            "rows_saved": len(entries),
            "source_ids": [s.id for s, _ in entries],
        }
        self._repo.create_sources_batch(entries, user.name, batch)
        return BulkSaveResult(
            batch_id=batch["batch_id"],
            saved=len(entries),
            skipped=len(results) - len(entries),
            rows=results,
        )

    def errors_csv(self, user: AppUser, rows: list[dict[str, str]]) -> str:
        """errors.csv: the rows that failed, with the reason for each."""
        require_role(user, CONFIG_ROLES, "bulk add sources")
        cleaned = [
            {k: _cell(v) for k, v in r.items() if k in ALLOWED_COLUMNS} for r in rows[:MAX_ROWS]
        ]
        out = io.StringIO()
        writer = csv.writer(out, lineterminator="\r\n")
        writer.writerow([*COLUMNS, "reason"])
        for res in self._check(cleaned):
            if res.status == "ERROR":
                reasons = "; ".join(r for r in res.reasons)
                writer.writerow(
                    [_safe_cell(res.raw.get(c, "")) for c in COLUMNS] + [_safe_cell(reasons)]
                )
        return out.getvalue()


def decide_many(
    items: list[BulkDecisionItem], act: Callable[[str, int], object]
) -> list[BulkDecisionResult]:
    """Apply one decision per source. One failure (for example "you made this one") never stops
    the others; each result says why."""
    out = []
    for item in items:
        try:
            act(item.source_id, item.version)
            out.append(BulkDecisionResult(source_id=item.source_id, version=item.version, ok=True))
        except DomainError as exc:
            out.append(
                BulkDecisionResult(
                    source_id=item.source_id, version=item.version, ok=False, message=exc.message
                )
            )
    return out


# --- politeness for "Test selected" -----------------------------------------------------------


class HostGate:
    """One request per host at a time, >= 2 s between requests to a host, and a circuit breaker.

    After `trip_after` blocked or failed tests in a row on one host the breaker opens for
    `cool_down` seconds and further tests on that host are skipped without any request.
    """

    def __init__(
        self,
        min_gap: float = 2.0,
        trip_after: int = 3,
        cool_down: float = 900.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._gap, self._trip, self._cool = min_gap, trip_after, cool_down
        self._clock, self._sleep = clock, sleep
        self._guard = threading.Lock()
        self._locks: dict[str, threading.Lock] = {}
        self._last: dict[str, float] = {}
        self._fails: dict[str, int] = {}
        self._open_until: dict[str, float] = {}

    def run(self, source: Source, test: Callable[[Source], SourceTestResult]) -> SourceTestResult:
        host = (urlparse(source.url).hostname or "").lower()
        with self._guard:
            lock = self._locks.setdefault(host, threading.Lock())
        with lock:
            now = self._clock()
            if self._open_until.get(host, 0) > now:
                return SourceTestResult(
                    source_id=source.id,
                    status=CheckStatus.BLOCKED,
                    duration_seconds=0,
                    rows=[],
                    message=(
                        f"Skipped. {host} blocked or failed {self._trip} tests in a row, so "
                        "tests on it are paused for a while. Fetch this page by hand instead."
                    ),
                )
            wait = self._last.get(host, -1e9) + self._gap - now
            if wait > 0:
                self._sleep(wait)
            try:
                result = test(source)
            finally:
                self._last[host] = self._clock()
            if result.status not in (CheckStatus.BLOCKED, CheckStatus.FAILED):
                self._fails[host] = 0
            else:
                self._fails[host] = self._fails.get(host, 0) + 1
                if self._fails[host] >= self._trip:
                    self._open_until[host] = self._clock() + self._cool
                    self._fails[host] = 0
            return result
