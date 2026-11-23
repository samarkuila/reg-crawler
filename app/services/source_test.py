"""Test a source without saving anything ("Test this source" on the Sources page).

Two testers behind one small interface:

- `SubprocessSourceTester` runs the real Scrapy spider as a separate process in dry-run mode
  (CLAUDE.md rule 10: Scrapy never runs inside the FastAPI process). The spider reads ONE page,
  politely (robots.txt, identifying User-Agent, delay), writes no database rows and no stored files,
  and leaves its parsed rows in a temporary file that this class reads back.
- `MockSourceTester` gives fixed demo rows for the in-memory demo backend.

A block (403/429, bot check, login) is reported as "Blocked" and the test stops there; there is no
retry and no workaround.
"""

import json
import subprocess
import sys
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Protocol

from app.schemas.api import SourceTestResult, SourceTestRow
from app.schemas.entities import Source
from app.schemas.enums import CheckStatus, SourceType

ROOT = Path(__file__).resolve().parents[2]
SPIDERS = {
    SourceType.HTML_LIST: "listing",
    SourceType.BROWSER: "listing",
    SourceType.RSS: "rss",
    SourceType.STRUCTURED_DATA: "unsc_xml",
}
TIMEOUT_SECONDS = 120


class SourceTester(Protocol):
    def run(self, source: Source) -> SourceTestResult: ...


class MockSourceTester:
    """Fixed rows for the demo (in-memory) backend; nothing is fetched."""

    def run(self, source: Source) -> SourceTestResult:
        if source.id == "egazette":
            return SourceTestResult(
                source_id=source.id,
                status=CheckStatus.BLOCKED,
                duration_seconds=1.4,
                rows=[],
                message="The site showed a verification page. The test stopped; nothing was saved.",
            )
        rows = [
            SourceTestRow(
                title=f"{source.authority} sample row {n}",
                link=f"{source.url.rstrip('/')}/sample-{n}",
                displayed_date=date(2026, 11, 20),
                ref_no=None,
            )
            for n in (1, 2, 3)
        ]
        return SourceTestResult(
            source_id=source.id,
            status=CheckStatus.OK,
            duration_seconds=1.2,
            rows=rows,
            message=(
                "Mock result (demo backend): these rows are made up. "
                "Use the SQL backend to test for real."
            ),
        )


class SubprocessSourceTester:
    def __init__(self, timeout: int = TIMEOUT_SECONDS, fixture: str | None = None) -> None:
        self._timeout = timeout
        self._fixture = fixture  # tests: read a saved page instead of the website

    def run(self, source: Source) -> SourceTestResult:
        started = time.monotonic()
        spider = SPIDERS.get(source.type, "listing")
        with tempfile.TemporaryDirectory(prefix="source-test-") as tmp:
            folder = Path(tmp)
            source_file, out_file = folder / "source.json", folder / "result.json"
            source_file.write_text(source.model_dump_json(), encoding="utf-8")
            command = [
                sys.executable,
                "-m",
                "scrapy",
                "crawl",
                spider,
                "-a",
                "dry_run=1",
                "-a",
                f"source_json={source_file}",
                "-a",
                f"out={out_file}",
                "-s",
                "LOG_FILE=",
                "-s",
                "LOG_LEVEL=WARNING",
            ]
            if self._fixture:
                command += ["-a", f"fixture={self._fixture}"]
            try:
                done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
                    command,
                    cwd=ROOT / "crawler",
                    capture_output=True,
                    text=True,
                    timeout=self._timeout,
                    check=False,
                )
            except subprocess.TimeoutExpired:
                return self._result(
                    source,
                    started,
                    CheckStatus.FAILED,
                    f"The test took longer than {self._timeout} s and was stopped.",
                )
            if not out_file.is_file():
                return self._result(
                    source,
                    started,
                    CheckStatus.FAILED,
                    "The test could not run. " + _tail(done.stderr or done.stdout),
                )
            payload = json.loads(out_file.read_text(encoding="utf-8"))
        status = CheckStatus(payload["status"])
        rows = [SourceTestRow(**row) for row in payload["rows"]]
        return self._result(source, started, status, _message(status, payload), rows=rows)

    @staticmethod
    def _result(
        source: Source,
        started: float,
        status: CheckStatus,
        message: str,
        rows: list[SourceTestRow] | None = None,
    ) -> SourceTestResult:
        return SourceTestResult(
            source_id=source.id,
            status=status,
            duration_seconds=round(time.monotonic() - started, 1),
            rows=rows or [],
            message=message,
        )


def _tail(text: str, lines: int = 3) -> str:
    kept = [ln.strip() for ln in text.strip().splitlines() if ln.strip()][-lines:]
    return " ".join(kept)[:400]


def _message(status: CheckStatus, payload: dict) -> str:  # type: ignore[type-arg]
    detail = (payload.get("detail") or "").strip()
    found = payload.get("rows_found", 0)
    if status == CheckStatus.OK:
        text = f"Read {found} row(s) from the page. Nothing was saved."
    elif status == CheckStatus.ZERO_ITEMS:
        text = "The page was read but no rows were found. Nothing was saved."
    elif status == CheckStatus.BLOCKED:
        text = (
            "The site showed a verification page or refused access. The test stopped; "
            "nothing was saved."
        )
    else:
        text = "The test did not get usable rows. Nothing was saved."
    return f"{text} {detail}".strip()
