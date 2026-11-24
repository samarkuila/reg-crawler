"""Crawl runner (architecture §3.1): one run = every active source, then detection and documents.

    start() ─► create crawl_run (RUNNING), take the lock ─► in the background:
        for each active, approved source (one at a time, so one request at a time per host):
            launch `scrapy crawl <spider> -a source=<id> -a run_id=<id>` as a SUBPROCESS (rule 10)
            read what it left in source_check; if it left nothing, record FAILED with the exit code
        ─► Detection.process(run) ─► Documents.process(events) ─► alerts
        ─► close the run COMPLETED or PARTIAL

Rules kept here:
- One failure never stops the others. A source that is blocked, fails, times out or crashes is
  recorded and the run moves on (no retry with altered requests: that is the crawler's rule 1).
- No overlapping runs: an exclusive lock file, refreshed while the run goes. A second start is
  refused with a polite message. A lock nobody refreshed for 30 minutes belongs to a dead process
  and is taken over (the interrupted run is closed as PARTIAL).
- The run is always closed, whatever goes wrong.
- Single worker: the lock and the background thread live in one process (POC; run uvicorn with
  one worker).
"""

import logging
import os
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urlparse

from app.repositories.base import Repository
from app.schemas.api import CrawlStatus
from app.schemas.entities import Alert, CrawlRun, Source, SourceCheck, now_ist
from app.schemas.enums import (
    AlertKind,
    BaselineStatus,
    CheckChannel,
    CheckStatus,
    Cycle,
    Priority,
    RunStatus,
    SourceType,
)
from app.services.detection import DetectionResult, DetectionService
from app.services.documents import DocumentService
from app.services.errors import ConflictError, InvalidError, NotFoundError
from app.services.first_run import load_first_run_config, window_for
from app.services.permissions import RUN_ROLES, require_role
from app.services.robots import RobotsCheck

log = logging.getLogger(__name__)
ACTOR = "crawl-runner"
ROOT = Path(__file__).resolve().parents[2]
MAX_REQUESTS_PER_HOST = 50  # page requests to one website in one run (never exceeded)
SOURCE_TIMEOUT_SECONDS = 300
STALE_LOCK_SECONDS = 30 * 60

_STATUS_ALERTS = {
    CheckStatus.FAILED: (AlertKind.SOURCE_FAILED, "urgent", "could not be read"),
    CheckStatus.BLOCKED: (
        AlertKind.SOURCE_BLOCKED,
        "urgent",
        "blocked the crawler (nothing was bypassed). Please check it by hand",
    ),
    CheckStatus.ZERO_ITEMS: (AlertKind.SOURCE_ZERO_ITEMS, "warning", "returned no items"),
    CheckStatus.PARSE_ERROR: (
        AlertKind.SOURCE_PARSE_ERROR,
        "warning",
        "answered, but the page could not be read",
    ),
}


# --- which spiders read a source ---------------------------------------------------------------


def spiders_for(source: Source) -> list[tuple[str, CheckChannel]]:
    """(spider, channel) pairs for a source: the feed first, then the page."""
    if source.type == SourceType.STRUCTURED_DATA:
        return [("unsc_xml", CheckChannel.XML)]
    pairs: list[tuple[str, CheckChannel]] = []
    if source.rss_url:
        pairs.append(("rss", CheckChannel.RSS))
    if source.type in (SourceType.HTML_LIST, SourceType.BROWSER):
        pairs.append(("listing", CheckChannel.HTML))
    return pairs


# --- launching a spider ------------------------------------------------------------------------


@dataclass
class LaunchResult:
    exit_code: int | None
    log_tail: str = ""
    timed_out: bool = False
    error: str | None = None  # the process could not even be started


class SpiderLauncher(Protocol):
    def launch(
        self,
        spider: str,
        source_id: str,
        run_id: str,
        lookback_days: int | None = None,
        first_run: bool = False,
        first_run_window: str | None = None,
        max_requests: int | None = None,
    ) -> LaunchResult: ...


class SubprocessLauncher:
    """Runs `scrapy crawl` as a separate process, with its log under data/logs/runs/<run>/.

    `fixtures` maps (source id, spider) to a saved file for offline runs (tests, demos); live runs
    leave it empty.
    """

    def __init__(
        self,
        timeout: int = SOURCE_TIMEOUT_SECONDS,
        fixtures: dict[tuple[str, str], str] | None = None,
        env: dict[str, str] | None = None,
    ) -> None:
        self._timeout = timeout
        self._fixtures = fixtures or {}
        self._env = env or {}

    def launch(
        self,
        spider: str,
        source_id: str,
        run_id: str,
        lookback_days: int | None = None,
        first_run: bool = False,
        first_run_window: str | None = None,
        max_requests: int | None = None,
    ) -> LaunchResult:
        files_root = Path(os.environ.get("FILES_ROOT") or ROOT).resolve()
        log_file = files_root / "data" / "logs" / "runs" / run_id / f"{source_id}-{spider}.log"
        log_file.parent.mkdir(parents=True, exist_ok=True)
        command = [
            sys.executable, "-m", "scrapy", "crawl", spider,
            "-a", f"source={source_id}",
            "-a", f"run_id={run_id}",
            "-s", f"LOG_FILE={log_file}",
        ]  # fmt: skip
        if lookback_days:
            command += ["-a", f"lookback_days={lookback_days}"]
        if first_run:
            command += ["-a", "first_run=1", "-a", f"first_run_window={first_run_window or ''}"]
        if max_requests is not None:
            command += ["-a", f"max_requests={max_requests}"]
        fixture = self._fixtures.get((source_id, spider))
        if fixture:
            command += ["-a", f"fixture={fixture}"]
        try:
            done = subprocess.run(  # noqa: S603 - fixed argument list, no shell
                command,
                cwd=ROOT / "crawler",
                env={**os.environ, **self._env},
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except subprocess.TimeoutExpired:
            return LaunchResult(None, _tail(log_file), timed_out=True)
        except OSError as exc:
            return LaunchResult(None, error=f"The crawler could not be started: {exc}")
        return LaunchResult(done.returncode, _tail(log_file, done.stderr))


def _tail(log_file: Path, fallback: str = "", lines: int = 3) -> str:
    text = ""
    if log_file.is_file():
        text = log_file.read_text(encoding="utf-8", errors="replace")
    kept = [ln.strip() for ln in (text or fallback).splitlines() if ln.strip()][-lines:]
    return " | ".join(kept)[:400]


# --- the lock ----------------------------------------------------------------------------------


class RunLock:
    """An exclusive lock file shared by every process on this machine."""

    def __init__(self, path: Path | None = None, stale_seconds: int = STALE_LOCK_SECONDS) -> None:
        root = Path(os.environ.get("FILES_ROOT") or ROOT).resolve()
        self._path = path or root / "data" / "locks" / "crawl.lock"
        self._stale = stale_seconds

    def acquire(self, run_label: str) -> tuple[bool, bool]:
        """(acquired, took_over_a_stale_lock)."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        stale = False
        if self._path.exists() and time.time() - self._path.stat().st_mtime > self._stale:
            self._path.unlink(missing_ok=True)
            stale = True
        try:
            fd = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            return False, False
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(f"{run_label} pid={os.getpid()}\n")
        return True, stale

    def touch(self) -> None:
        try:
            os.utime(self._path)
        except OSError:
            pass

    def release(self) -> None:
        self._path.unlink(missing_ok=True)

    def held(self) -> bool:
        return self._path.exists() and time.time() - self._path.stat().st_mtime <= self._stale


# --- the runner --------------------------------------------------------------------------------


@dataclass
class RunSummary:
    run_id: str
    status: RunStatus
    problems: list[str] = field(default_factory=list)
    detection: DetectionResult | None = None


class CrawlRunner:
    def __init__(
        self,
        repo: Repository,
        launcher: SpiderLauncher,
        documents: DocumentService | None = None,
        agent: Any = None,
        lock: RunLock | None = None,
        disabled_reason: str | None = None,
        robots_factory: Callable[[], RobotsCheck] | None = None,
    ) -> None:
        self._repo = repo
        self._robots_factory = robots_factory  # None = no pre-check (tests, demo)
        self._robots: RobotsCheck | None = None
        self._launcher = launcher
        self._documents = documents
        self._agent = agent  # Luna (advisory); None or off = the run works exactly as before
        self._lock = lock or RunLock()
        self._disabled = disabled_reason
        self._thread: threading.Thread | None = None
        self._summaries: dict[str, RunSummary] = {}
        self._current: str | None = None  # the page being read (this process only)
        self._host_requests: dict[str, int] = {}  # page requests per website in this run
        self._phase: str | None = None

    # --- starting ----------------------------------------------------------------------------

    def start_for(self, user, source_id: str | None = None) -> CrawlRun:  # type: ignore[no-untyped-def]
        """Run now, for a signed-in person (reviewer, approver or admin)."""
        require_role(user, RUN_ROLES, "start a crawl")
        return self.start(Cycle.ADHOC, user.name, source_id)

    def start(
        self,
        cycle: Cycle,
        triggered_by: str,
        source_id: str | None = None,
        *,
        urgent_only: bool = False,
        background: bool = True,
    ) -> CrawlRun:
        """Create the run and start it. Raises ConflictError if another run is going."""
        sources = self._select(source_id, urgent_only)
        if self._disabled:
            raise InvalidError(self._disabled)
        acquired, took_over = self._lock.acquire(triggered_by)
        if not acquired:
            raise ConflictError(self._busy_message())
        try:
            if took_over:
                self._close_interrupted_runs()
            run = self._repo.create_run(
                cycle,
                now_ist().date(),
                triggered_by,
                triggered_by,
                sources=[s.id for s in sources] if (source_id or urgent_only) else None,
            )
        except BaseException:
            self._lock.release()
            raise
        if background:
            self._thread = threading.Thread(
                target=self._execute, args=(run, sources), name=f"crawl-{run.id}", daemon=True
            )
            self._thread.start()
        else:
            self._execute(run, sources)
        return run

    def wait(self, timeout: float | None = None) -> None:
        """Wait for the background run to finish (tests, shutdown)."""
        if self._thread is not None:
            self._thread.join(timeout)

    def summary(self, run_id: str) -> RunSummary | None:
        return self._summaries.get(run_id)

    @property
    def disabled_reason(self) -> str | None:
        return self._disabled

    def is_running(self) -> bool:
        return self._lock.held()

    _PHASES = {
        "reading": "Reading the regulator pages",
        "detecting": "Finding new and changed items",
        "documents": "Downloading documents",
        "screening": "Screening items",
    }

    def status(self) -> CrawlStatus:
        """What is happening now. Safe to poll: it only reads."""
        running = self._lock.held()
        run = next((r for r in self._repo.list_runs() if r.status == RunStatus.RUNNING), None)
        if not running or run is None:
            return CrawlStatus(running=False, message="No crawl is running.")
        active = {s.id for s in self._repo.list_sources() if s.active and not s.planned}
        scope = set(run.sources) if run.sources else active
        done = {c.source_id for c in self._repo.list_source_checks(run.id)} & scope
        mine = self._summaries.get(run.id) is not None
        phase = self._phase if mine else None
        return CrawlStatus(
            running=True,
            run=run,
            sources_done=len(done),
            sources_total=len(scope),
            current=self._current if mine and phase == "reading" else None,
            phase=phase,
            message=self._PHASES.get(phase or "", "Crawl in progress"),
        )

    def _busy_message(self) -> str:
        running = [r for r in self._repo.list_runs() if r.status == RunStatus.RUNNING]
        if running:
            r = running[0]
            when = r.started_at.strftime("%H:%M")
            return (
                f"A crawl is already running (started {when} by {r.triggered_by}). "
                "Please wait until it finishes; progress is on the Source health page."
            )
        return "A crawl is already running. Please wait until it finishes."

    def _select(self, source_id: str | None, urgent_only: bool) -> list[Source]:
        every = self._repo.list_sources()
        if source_id is not None:
            source = next((s for s in every if s.id == source_id), None)
            if source is None:
                raise NotFoundError(f"Source {source_id} not found")
            if not source.active or source.planned:
                raise InvalidError(
                    f"{source.authority} · {source.page_name} is not active (it is planned or "
                    "waiting for approval), so it is not crawled."
                )
            return [source]
        chosen = [s for s in every if s.active and not s.planned]
        if urgent_only:
            chosen = [s for s in chosen if s.priority == Priority.URGENT]
        if not chosen:
            raise InvalidError("There are no active sources to crawl.")
        return chosen

    def _close_interrupted_runs(self) -> None:
        for run in self._repo.list_runs():
            if run.status == RunStatus.RUNNING:
                self._repo.close_run(run.id, RunStatus.PARTIAL, ACTOR)
                self._repo.add_alerts(
                    [
                        self._alert(
                            run.id,
                            AlertKind.RUN_PROBLEM,
                            "urgent",
                            "An earlier run was interrupted (the app stopped while it was "
                            "running) and was closed as incomplete.",
                        )
                    ],
                    ACTOR,
                )

    # --- the run -----------------------------------------------------------------------------

    def _execute(self, run: CrawlRun, sources: list[Source]) -> None:
        summary = RunSummary(run.id, RunStatus.PARTIAL)
        self._summaries[run.id] = summary
        try:
            self._phase = "reading"
            self._host_requests = {}
            self._robots = self._robots_factory() if self._robots_factory else None
            for source in sources:
                self._lock.touch()
                self._current = f"{source.authority} · {source.page_name}"
                try:
                    self._crawl_source(run, source)
                except Exception as exc:  # one source never stops the others
                    log.exception("Source %s failed unexpectedly", source.id)
                    summary.problems.append(f"{source.page_name}: {exc}")
            self._lock.touch()
            self._current, self._phase = None, "detecting"
            summary.detection = self._detect(run, summary)
            self._lock.touch()
            self._phase = "documents"
            self._fetch_documents(summary)
            self._lock.touch()
            self._phase = "screening"
            self._run_agent(run, summary)
            self._record_alerts(run, summary)
        except Exception as exc:  # the run must still be closed below
            log.exception("Run %s failed", run.id)
            summary.problems.append(str(exc))
        finally:
            checks = self._repo.list_source_checks(run.id)
            healthy = checks and all(c.status == CheckStatus.OK for c in checks)
            summary.status = (
                RunStatus.COMPLETED if healthy and not summary.problems else RunStatus.PARTIAL
            )
            try:
                self._repo.close_run(run.id, summary.status, ACTOR)
            finally:
                self._current = self._phase = None
                self._lock.release()

    def _spider_args(self, source: Source, used: int) -> dict[str, Any]:
        """What the crawl of one source is told: its window and how many requests it may make.

        A source that is NOT_BASELINED gets the first-run window (items older than it are kept as
        "already seen" by detection, never dropped). A BASELINED source gets the `lookback-days`
        setting. Either way at most 50 page requests go to one website in one run.
        """
        args: dict[str, Any] = {"max_requests": MAX_REQUESTS_PER_HOST - used}
        if source.baseline_status == BaselineStatus.NOT_BASELINED:
            window = window_for(source, load_first_run_config())
            args["first_run"] = True
            args["first_run_window"] = window.start.isoformat() if window.start else None
            return args
        setting = self._repo.get_setting("lookback-days")
        value = setting.value if setting is not None else None
        ok = isinstance(value, int) and not isinstance(value, bool) and value >= 1
        args["lookback_days"] = value if ok else 2
        return args

    def _crawl_source(self, run: CrawlRun, source: Source) -> None:
        host = (urlparse(source.url).hostname or "").lower()
        reason = self._robots.blocked_reason(source.url) if self._robots else None
        if reason:  # the website's robots.txt says no: record it, request nothing, move on
            for _spider, channel in spiders_for(source):
                self._repo.record_source_check(
                    SourceCheck(
                        id=f"chk-{run.id}-{source.id}-{channel.value.lower()}",
                        run_id=run.id,
                        source_id=source.id,
                        channel=channel,
                        status=CheckStatus.BLOCKED,
                        error_detail=reason,
                    ),
                    ACTOR,
                )
            return
        for spider, channel in spiders_for(source):
            used = self._host_requests.get(host, 0)
            if used >= MAX_REQUESTS_PER_HOST:
                self._repo.record_source_check(
                    SourceCheck(
                        id=f"chk-{run.id}-{source.id}-{channel.value.lower()}",
                        run_id=run.id,
                        source_id=source.id,
                        channel=channel,
                        status=CheckStatus.FAILED,
                        error_detail=(
                            f"Not read: the limit of {MAX_REQUESTS_PER_HOST} page requests to "
                            f"{host} in one run was reached"
                        ),
                    ),
                    ACTOR,
                )
                continue
            started = time.monotonic()
            try:
                result = self._launcher.launch(
                    spider, source.id, run.id, **self._spider_args(source, used)
                )
            except Exception as exc:
                result = LaunchResult(None, error=f"The crawler could not be started: {exc}")
            self._reconcile(run, source, channel, result, time.monotonic() - started)
            made = next(
                (
                    int(c.stats.get("requests_made") or 0)
                    for c in self._repo.list_source_checks(run.id)
                    if c.source_id == source.id and c.channel == channel
                ),
                0,
            )
            self._host_requests[host] = used + made

    def _reconcile(
        self,
        run: CrawlRun,
        source: Source,
        channel: CheckChannel,
        result: LaunchResult,
        secs: float,
    ) -> None:
        """Make sure the channel has a source_check; note a bad exit code on the ones it left."""
        existing = [
            c
            for c in self._repo.list_source_checks(run.id)
            if c.source_id == source.id and c.channel == channel
        ]
        problem = None
        if result.error:
            problem = result.error
        elif result.timed_out:
            problem = f"Stopped after {SOURCE_TIMEOUT_SECONDS // 60} minutes without finishing"
        elif result.exit_code not in (0, None):
            problem = f"The crawler stopped with an error (exit code {result.exit_code})"
        if existing:
            if problem and existing[0].status == CheckStatus.OK:
                # It reported success but did not exit cleanly: say so, keep what it read.
                detail = (
                    f"{existing[0].error_detail}; {problem}"
                    if existing[0].error_detail
                    else problem
                )
                self._repo.record_source_check(
                    existing[0].model_copy(update={"error_detail": detail}), ACTOR
                )
            return
        detail = problem or "The crawler finished without reporting a result"
        if result.log_tail:
            detail += f". Log: {result.log_tail}"
        self._repo.record_source_check(
            SourceCheck(
                id=f"chk-{run.id}-{source.id}-{channel.value.lower()}",
                run_id=run.id,
                source_id=source.id,
                channel=channel,
                status=CheckStatus.FAILED,
                error_detail=detail[:900],
            ),
            ACTOR,
        )

    def _detect(self, run: CrawlRun, summary: RunSummary) -> DetectionResult | None:
        try:
            return DetectionService(self._repo).process(run.id)
        except Exception as exc:
            log.exception("Detection failed for %s", run.id)
            summary.problems.append(f"Detection failed: {exc}")
            return None

    def _fetch_documents(self, summary: RunSummary) -> None:
        if self._documents is None or summary.detection is None:
            return
        try:
            report = self._documents.process(summary.detection.events)
            summary.problems.extend(n for n in report.notes if "blocked" in n or "failed" in n)
        except Exception as exc:
            log.exception("Documents failed")
            summary.problems.append(f"Documents failed: {exc}")

    def _run_agent(self, run: CrawlRun, summary: RunSummary) -> None:
        """Luna after detection and documents. It cannot fail the run: problems are alerts."""
        if self._agent is None or summary.detection is None:
            return
        try:
            self._agent.process(run.id, summary.detection.events)
        except Exception:
            log.exception("Luna failed for %s", run.id)  # the items simply have no suggestions

    # --- alerts ------------------------------------------------------------------------------

    @staticmethod
    def _alert(run_id, kind, severity, message, source_id=None, item_id=None) -> Alert:  # type: ignore[no-untyped-def]
        return Alert(
            id=f"alr-{uuid.uuid4().hex[:10]}",
            run_id=run_id,
            kind=kind,
            severity=severity,
            source_id=source_id,
            item_id=item_id,
            message=message,
            created_at=now_ist(),
        )

    def _record_alerts(self, run: CrawlRun, summary: RunSummary) -> None:
        alerts: list[Alert] = []
        sources = {s.id: s for s in self._repo.list_sources()}
        for check in self._repo.list_source_checks(run.id):
            spec = _STATUS_ALERTS.get(check.status)
            if spec is None:
                continue
            kind, severity, phrase = spec
            source = sources.get(check.source_id)
            name = f"{source.authority} · {source.page_name}" if source else check.source_id
            detail = f" {check.error_detail}" if check.error_detail else ""
            alerts.append(
                self._alert(
                    run.id,
                    kind,
                    severity,
                    f"{name} ({check.channel.value}) {phrase}.{detail}",
                    check.source_id,
                )  # fmt: skip
            )
        if summary.detection is not None:
            for warning in summary.detection.warnings:
                if warning.kind == "DROP":
                    alerts.append(
                        self._alert(
                            run.id,
                            AlertKind.SANITY_WARNING,
                            "warning",
                            warning.detail,
                            warning.source_id,
                        )  # fmt: skip
                    )
            for event in summary.detection.events:
                if event.item.priority == Priority.URGENT:
                    alerts.append(
                        self._alert(
                            run.id,
                            AlertKind.URGENT_ITEM,
                            "urgent",
                            f"Urgent: {event.item.title}",
                            event.item.source_id,
                            event.item.id,
                        )  # fmt: skip
                    )
        for problem in summary.problems:
            alerts.append(self._alert(run.id, AlertKind.RUN_PROBLEM, "warning", problem))
        self._repo.add_alerts(alerts, ACTOR)
