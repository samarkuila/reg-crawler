"""A fake crawler for runner tests: it writes what a real spider would leave in the database (raw
rows and a source_check per channel) without starting a process or touching the network."""

import threading
from collections.abc import Callable

from app.schemas.entities import SourceCheck
from app.schemas.enums import CheckChannel, CheckStatus
from app.services.crawl_runner import LaunchResult
from tests.detection_helpers import crawl, crawl_xml, row

CHANNEL = {"rss": CheckChannel.RSS, "listing": CheckChannel.HTML, "unsc_xml": CheckChannel.XML}


def write_rows(repo, spider: str, source_id: str, run_id: str, rows: list[dict]) -> None:
    run = repo.get_run(run_id)
    if spider == "rss":
        crawl(repo, source_id, run.cycle_date, rss=rows, run=run)
    elif spider == "listing":
        crawl(repo, source_id, run.cycle_date, html=rows, run=run)
    else:
        crawl_xml(repo, source_id, run.cycle_date, rows, run=run)


def write_status(repo, spider: str, source_id: str, run_id: str, status: CheckStatus, **kw) -> None:
    repo.record_source_check(
        SourceCheck(
            id=f"chk-{run_id}-{source_id}-{CHANNEL[spider].value.lower()}",
            run_id=run_id,
            source_id=source_id,
            channel=CHANNEL[spider],
            status=status,
            **kw,
        ),
        "crawler",
    )


class FakeLauncher:
    """Every (source, spider) writes one OK row unless `script` says otherwise.

    `script[(source_id, spider)]` is a function (repo, spider, source_id, run_id) -> LaunchResult
    | None, or an exception to raise. `before` runs first (for progress and overlap tests).
    """

    def __init__(self, repo, script=None, before: Callable | None = None) -> None:
        self.repo = repo
        self.script = script or {}
        self.before = before
        self.calls: list[tuple[str, str]] = []

    def launch(self, spider: str, source_id: str, run_id: str) -> LaunchResult:
        self.calls.append((source_id, spider))
        if self.before:
            self.before(source_id, spider, run_id)
        action = self.script.get((source_id, spider))
        if isinstance(action, BaseException):
            raise action
        if action is not None:
            return action(self.repo, spider, source_id, run_id) or LaunchResult(0)
        write_rows(
            self.repo,
            spider,
            source_id,
            run_id,
            [row(f"{source_id} notice", f"{source_id}/{spider}/1")],
        )
        return LaunchResult(0)


class Gate:
    """Blocks a launch until released, so a test can start a second run while one is going."""

    def __init__(self) -> None:
        self.entered = threading.Event()
        self.release = threading.Event()

    def __call__(self, source_id: str, spider: str, run_id: str) -> None:
        self.entered.set()
        assert self.release.wait(10), "the test never released the crawl"
