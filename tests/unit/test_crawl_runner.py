"""CrawlRunner: whole runs, failures that do not stop the rest, the lock, alerts, always closed."""

import os
import time
from types import SimpleNamespace

import pytest

from app.schemas.entities import Alert, now_ist
from app.schemas.enums import AlertKind, CheckStatus, Cycle, RunStatus, SourceType
from app.services import detection
from app.services.crawl_runner import (
    CrawlRunner,
    LaunchResult,
    RunLock,
    SubprocessLauncher,
    spiders_for,
)
from app.services.errors import ConflictError, ForbiddenError, InvalidError, NotFoundError
from app.services.review import ReviewService
from tests.conftest import BACKENDS, make_backend
from tests.crawl_helpers import FakeLauncher, Gate, write_rows, write_status
from tests.detection_helpers import make_source, row

OK = LaunchResult(0)


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path, sql_template):
    repository, _url, _files = make_backend(request.param, tmp_path, sql_template)
    return repository


@pytest.fixture
def lock(tmp_path):
    return RunLock(tmp_path / "crawl.lock")


def runner(repo, lock, launcher=None, **kw):
    return CrawlRunner(repo, launcher or FakeLauncher(repo), lock=lock, **kw)


def active_pairs(repo):
    return [
        (s.id, spider)
        for s in repo.list_sources()
        if s.active and not s.planned
        for spider, _ch in spiders_for(s)
    ]


def checks_of(repo, run_id):
    return {(c.source_id, c.channel.value): c for c in repo.list_source_checks(run_id)}


# --- which spiders ----------------------------------------------------------------------------


def test_spiders_for_each_kind_of_source(repo) -> None:
    both = make_source(repo, "both")  # HTML page with a feed
    assert [s for s, _ in spiders_for(both)] == ["rss", "listing"]  # the feed first
    assert [s for s, _ in spiders_for(repo.get_source("unsc"))] == ["unsc_xml"]
    assert [s for s, _ in spiders_for(repo.get_source("rbi"))] == ["rss"]
    page_only = make_source(repo, "page", rss_url=None)
    assert [s for s, _ in spiders_for(page_only)] == ["listing"]
    assert [
        s for s, _ in spiders_for(page_only.model_copy(update={"type": SourceType.BROWSER}))
    ] == ["listing"]


# --- a whole run --------------------------------------------------------------------------------


def test_a_run_crawls_every_active_source_then_detects_and_closes(repo, lock) -> None:
    launcher = FakeLauncher(repo)
    run = runner(repo, lock, launcher).start(Cycle.ADHOC, "Divya G.", background=False)
    assert sorted(launcher.calls) == sorted(active_pairs(repo))
    closed = repo.get_run(run.id)
    assert closed.status == RunStatus.COMPLETED and closed.ended_at is not None
    assert closed.triggered_by == "Divya G." and closed.sources is None
    assert {c.status for c in repo.list_source_checks(run.id)} == {CheckStatus.OK}
    items = [
        i
        for i in repo.list_items(__import__("app.schemas.api", fromlist=["x"]).ItemFilter())
        if i.first_seen_run_id == run.id
    ]
    assert len(items) == len(active_pairs(repo))  # detection ran on what the crawl stored
    assert not lock.held()  # released
    actions = {e.action for e in repo.list_audit()}
    assert {"crawl_run.created", "crawl_run.closed"} <= actions


def test_the_lock_is_released_and_a_second_run_can_follow(repo, lock) -> None:
    r = runner(repo, lock)
    r.start(Cycle.ADHOC, "A", "rbi", background=False)
    run2 = r.start(Cycle.ADHOC, "B", "rbi", background=False)
    assert repo.get_run(run2.id).status in (RunStatus.COMPLETED, RunStatus.PARTIAL)


def test_a_one_source_run_covers_only_that_source(repo, lock) -> None:
    launcher = FakeLauncher(repo)
    run = runner(repo, lock, launcher).start(Cycle.ADHOC, "A", "unsc", background=False)
    assert launcher.calls == [("unsc", "unsc_xml")] and repo.get_run(run.id).sources == ["unsc"]
    detail = ReviewService(repo).run_detail(run.id)
    assert (detail.sources_total, detail.sources_done) == (1, 1)  # not "1 of 11"


def test_urgent_runs_cover_only_urgent_sources(repo, lock) -> None:
    launcher = FakeLauncher(repo)
    urgent = [s.id for s in repo.list_sources() if s.active and s.priority.value == "URGENT"]
    assert urgent
    runner(repo, lock, launcher).start(
        Cycle.URGENT, "scheduler", urgent_only=True, background=False
    )
    assert {src for src, _ in launcher.calls} == set(urgent)


def test_progress_is_visible_while_the_run_goes(repo, lock) -> None:
    seen = []

    def watch(source_id, spider, run_id):
        d = ReviewService(repo).run_detail(run_id)
        seen.append((d.run.status.value, d.sources_done, d.sources_total))

    runner(repo, lock, FakeLauncher(repo, before=watch)).start(Cycle.ADHOC, "A", background=False)
    assert seen[0][0] == "RUNNING" and seen[0][1] == 0
    assert max(done for _s, done, _t in seen) >= 2 and {t for _s, _d, t in seen} == {seen[0][2]}


# --- failures never stop the others -------------------------------------------------------------


def test_one_source_raising_does_not_stop_the_rest(repo, lock) -> None:
    launcher = FakeLauncher(repo, script={("trai", "listing"): RuntimeError("boom")})
    run = runner(repo, lock, launcher).start(Cycle.ADHOC, "A", background=False)
    checks = checks_of(repo, run.id)
    assert (
        checks[("trai", "HTML")].status == CheckStatus.FAILED
        and "boom" in checks[("trai", "HTML")].error_detail
    )
    assert len(launcher.calls) == len(active_pairs(repo))  # every source was still tried
    assert checks[("unsc", "XML")].status == CheckStatus.OK
    assert repo.get_run(run.id).status == RunStatus.PARTIAL


def test_a_crashed_crawler_is_recorded_with_its_exit_code_and_log(repo, lock) -> None:
    crash = LaunchResult(1, log_tail="ValueError: Source 'x' is not active")
    launcher = FakeLauncher(repo, script={("trai", "listing"): lambda *a: crash})
    run = runner(repo, lock, launcher).start(Cycle.ADHOC, "A", "trai", background=False)
    check = checks_of(repo, run.id)[("trai", "HTML")]
    assert check.status == CheckStatus.FAILED
    assert "exit code 1" in check.error_detail and "is not active" in check.error_detail


def test_a_crawler_that_times_out_is_failed(repo, lock) -> None:
    timeout = LaunchResult(None, timed_out=True)
    launcher = FakeLauncher(repo, script={("trai", "listing"): lambda *a: timeout})
    run = runner(repo, lock, launcher).start(Cycle.ADHOC, "A", "trai", background=False)
    assert "Stopped after 5 minutes" in checks_of(repo, run.id)[("trai", "HTML")].error_detail


def test_a_crawler_that_cannot_start_is_failed(repo, lock) -> None:
    launcher = FakeLauncher(
        repo, script={("trai", "listing"): lambda *a: LaunchResult(None, error="no python")}
    )
    run = runner(repo, lock, launcher).start(Cycle.ADHOC, "A", "trai", background=False)
    assert "no python" in checks_of(repo, run.id)[("trai", "HTML")].error_detail


def test_a_crawler_that_reported_ok_but_exited_badly_keeps_its_rows_and_says_so(repo, lock) -> None:
    def ok_then_crash(repo_, spider, source_id, run_id):
        write_rows(repo_, spider, source_id, run_id, [row("Notice", "T/1")])
        return LaunchResult(1)

    launcher = FakeLauncher(repo, script={("trai", "listing"): ok_then_crash})
    run = runner(repo, lock, launcher).start(Cycle.ADHOC, "A", "trai", background=False)
    check = checks_of(repo, run.id)[("trai", "HTML")]
    assert check.status == CheckStatus.OK and "exit code 1" in check.error_detail
    assert len(repo.list_raw_items(run.id, "trai")) == 1


def test_a_blocked_source_is_recorded_and_the_run_is_partial(repo, lock) -> None:
    blocked = lambda r, sp, src, run_id: write_status(  # noqa: E731
        r, sp, src, run_id, CheckStatus.BLOCKED, http_status=403, error_detail="HTTP 403"
    )
    launcher = FakeLauncher(repo, script={("trai", "listing"): blocked})
    run = runner(repo, lock, launcher).start(Cycle.ADHOC, "A", background=False)
    assert repo.get_run(run.id).status == RunStatus.PARTIAL
    kinds = {a.kind for a in repo.list_alerts(run.id)}
    assert AlertKind.SOURCE_BLOCKED in kinds


# --- alerts -------------------------------------------------------------------------------------


def test_alerts_are_stored_for_problem_sources_and_urgent_items(repo, lock) -> None:
    def zero(r, sp, src, run_id):
        write_rows(r, sp, src, run_id, [])  # a healthy but empty listing: ZERO_ITEMS

    def urgent(r, sp, src, run_id):
        write_rows(r, sp, src, run_id, [row("UNSC amended: X", "QDi.1", None, priority="URGENT")])

    launcher = FakeLauncher(
        repo,
        script={
            ("trai", "listing"): zero,
            ("unsc", "unsc_xml"): urgent,
            ("irdai", "listing"): lambda *a: LaunchResult(2, log_tail="Traceback"),
        },
    )
    run = runner(repo, lock, launcher).start(Cycle.ADHOC, "A", background=False)
    alerts = {a.kind: a for a in repo.list_alerts(run.id)}
    assert (
        AlertKind.SOURCE_ZERO_ITEMS in alerts
        and alerts[AlertKind.SOURCE_ZERO_ITEMS].severity == "warning"
    )
    assert (
        AlertKind.SOURCE_FAILED in alerts and alerts[AlertKind.SOURCE_FAILED].severity == "urgent"
    )
    urgent_alert = alerts[AlertKind.URGENT_ITEM]
    assert urgent_alert.severity == "urgent" and "UNSC amended: X" in urgent_alert.message
    assert urgent_alert.item_id and urgent_alert.source_id == "unsc"
    assert all(a.delivered_at is None for a in alerts.values())  # waiting for the V2 notifier


def test_a_clean_run_of_a_normal_source_has_no_alerts(repo, lock) -> None:
    make_source(repo, "clean", rss_url=None)  # no history, so nothing to compare with
    run = runner(repo, lock).start(Cycle.ADHOC, "A", "clean", background=False)
    assert repo.list_alerts(run.id) == [] and repo.get_run(run.id).status == RunStatus.COMPLETED


# --- the run is always closed -------------------------------------------------------------------


def test_a_detection_failure_still_closes_the_run_and_alerts(repo, lock, monkeypatch) -> None:
    def boom(self, run_id):
        raise RuntimeError("detection exploded")

    monkeypatch.setattr(detection.DetectionService, "process", boom)
    run = runner(repo, lock).start(Cycle.ADHOC, "A", "unsc", background=False)
    assert repo.get_run(run.id).status == RunStatus.PARTIAL and not lock.held()
    assert any("detection exploded" in a.message for a in repo.list_alerts(run.id))


def test_a_documents_failure_is_reported_not_fatal(repo, lock) -> None:
    class BadDocs:
        def process(self, events):
            raise RuntimeError("disk full")

    run = runner(repo, lock, documents=BadDocs()).start(Cycle.ADHOC, "A", "unsc", background=False)
    assert repo.get_run(run.id).status == RunStatus.PARTIAL
    assert any("Documents failed" in a.message for a in repo.list_alerts(run.id))


def test_documents_get_the_detected_events(repo, lock) -> None:
    seen = []

    class Docs:
        def process(self, events):
            seen.extend(events)
            return SimpleNamespace(notes=["Some notice: blocked (HTTP 403); needs a manual fetch"])

    run = runner(repo, lock, documents=Docs()).start(Cycle.ADHOC, "A", "unsc", background=False)
    assert len(seen) == 1 and seen[0].item.source_id == "unsc"
    assert repo.get_run(run.id).status == RunStatus.PARTIAL  # a blocked document is a problem
    assert any("needs a manual fetch" in a.message for a in repo.list_alerts(run.id))


# --- no overlapping runs --------------------------------------------------------------------------


def test_a_second_start_while_running_is_refused_politely(repo, lock) -> None:
    gate = Gate()
    r = runner(repo, lock, FakeLauncher(repo, before=gate))
    first = r.start(Cycle.ADHOC, "Divya G.", "unsc")  # background
    assert gate.entered.wait(10)
    with pytest.raises(ConflictError) as err:
        r.start(Cycle.ADHOC, "George K.")
    assert "already running" in err.value.message and "Divya G." in err.value.message
    assert (
        len([x for x in repo.list_runs() if x.status == RunStatus.RUNNING]) == 1
    )  # no 2nd run row
    assert r.is_running()
    gate.release.set()
    r.wait(20)
    assert repo.get_run(first.id).status == RunStatus.COMPLETED and not r.is_running()
    r.start(Cycle.ADHOC, "George K.", "unsc", background=False)  # now it is allowed


def test_two_runners_share_one_lock(repo, lock) -> None:
    # A second process (the CLI, another app instance) uses the same lock file.
    gate = Gate()
    a = runner(repo, lock, FakeLauncher(repo, before=gate))
    a.start(Cycle.ADHOC, "A", "unsc")
    assert gate.entered.wait(10)
    with pytest.raises(ConflictError):
        runner(repo, lock).start(Cycle.ADHOC, "B", "unsc")
    gate.release.set()
    a.wait(20)


def test_a_stale_lock_is_taken_over_and_the_interrupted_run_is_closed(repo, lock) -> None:
    dead = repo.create_run(Cycle.ADHOC, now_ist().date(), "someone", "someone")  # RUNNING forever
    lock.acquire("dead process")
    old = time.time() - 3 * 3600
    os.utime(lock._path, (old, old))
    run = runner(repo, lock).start(Cycle.ADHOC, "A", "unsc", background=False)
    assert repo.get_run(dead.id).status == RunStatus.PARTIAL
    assert any("interrupted" in a.message for a in repo.list_alerts(dead.id))
    assert repo.get_run(run.id).status == RunStatus.COMPLETED


# --- who and what may run ---------------------------------------------------------------


def test_only_some_roles_can_run_now(repo, lock) -> None:
    r = runner(repo, lock)
    audit = next(u for u in repo.list_users() if u.role.value == "viewer")
    with pytest.raises(ForbiddenError):
        r.start_for(audit)
    reviewer = next(u for u in repo.list_users() if u.role.value == "reviewer")
    run = r.start_for(reviewer, "unsc")
    r.wait(20)
    assert run.triggered_by == reviewer.name and run.cycle == Cycle.ADHOC


def test_unknown_inactive_and_planned_sources_are_refused(repo, lock) -> None:
    r = runner(repo, lock)
    with pytest.raises(NotFoundError):
        r.start(Cycle.ADHOC, "A", "nope", background=False)
    planned = next(s for s in repo.list_sources() if s.planned)
    with pytest.raises(InvalidError, match="not active"):
        r.start(Cycle.ADHOC, "A", planned.id, background=False)
    assert repo.list_runs() == [] or all(x.triggered_by != "A" for x in repo.list_runs())
    assert not lock.held()  # a refused start never takes the lock


def test_a_disabled_runner_says_why(repo, lock) -> None:
    r = runner(repo, lock, disabled_reason="Run now needs REPO_BACKEND=sql")
    with pytest.raises(InvalidError, match="REPO_BACKEND=sql"):
        r.start(Cycle.ADHOC, "A", "unsc", background=False)
    assert not lock.held()


def test_alerts_have_no_delete_or_update_path(repo) -> None:
    repo.add_alerts(
        [Alert(id="alr-1", kind=AlertKind.RUN_SKIPPED, message="m", created_at=now_ist())], "t"
    )
    assert not hasattr(repo, "delete_alert") and [a.id for a in repo.list_alerts()] == ["alr-1"]


# --- the real subprocess, offline --------------------------------------------------------


def test_run_now_with_the_real_crawler_on_a_saved_feed(tmp_path, sql_template, monkeypatch) -> None:
    repo, url, files = make_backend("sql", tmp_path, sql_template)
    monkeypatch.setenv("FILES_ROOT", str(files.parent))
    fixture = os.path.join(
        os.path.dirname(__file__), "..", "fixtures", "rss", "rbi_notifications_sample.xml"
    )
    launcher = SubprocessLauncher(
        timeout=120,
        fixtures={("rbi", "rss"): os.path.abspath(fixture)},
        env={"DATABASE_URL": url, "FILES_ROOT": str(files.parent)},
    )
    r = CrawlRunner(repo, launcher, lock=RunLock(tmp_path / "crawl.lock"))
    run = r.start(Cycle.ADHOC, "Samarendra K.", "rbi", background=False)
    check = checks_of(repo, run.id)[("rbi", "RSS")]
    assert check.status == CheckStatus.OK and check.items_found == 3, check.error_detail
    assert repo.get_run(run.id).status == RunStatus.COMPLETED
    new_items = [
        x
        for x in repo.list_items(__import__("app.schemas.api", fromlist=["x"]).ItemFilter())
        if x.first_seen_run_id == run.id
    ]
    assert len(new_items) == 3
    log = files.parent / "data" / "logs" / "runs" / run.id / "rbi-rss.log"
    assert log.is_file()  # the crawler's own log is kept per run and source


# --- Luna after the run (advisory; the run works the same without it) ---------------------------


def _luna(repo, enabled, script):
    from app.agents.luna.agent import LunaAgent
    from app.agents.orchestrator import AgentOrchestrator
    from app.services.llm import FakeLlmClient, LoggedLlm

    fake = FakeLlmClient(script)
    return AgentOrchestrator(
        repo, lambda: LunaAgent(LoggedLlm(fake)), enabled=lambda: enabled
    ), fake


def test_a_run_with_luna_on_stores_suggestions_for_its_new_items(repo, lock) -> None:
    from tests.unit.test_luna_orchestrator import SCRIPT

    make_source(repo, "clean2", rss_url=None)
    orch, fake = _luna(repo, True, SCRIPT)
    run = runner(repo, lock, agent=orch).start(Cycle.ADHOC, "A", "clean2", background=False)
    (item,) = [
        i for i in repo.list_items(__import__("app.schemas.api", fromlist=["x"]).ItemFilter())
        if i.first_seen_run_id == run.id
    ]  # fmt: skip
    assert repo.list_extractions(item.id) and fake.calls
    assert repo.get_run(run.id).status == RunStatus.COMPLETED


def test_a_run_with_luna_off_is_identical_and_calls_nothing(repo, lock) -> None:
    from tests.unit.test_luna_orchestrator import SCRIPT

    make_source(repo, "clean3", rss_url=None)
    orch, fake = _luna(repo, False, SCRIPT)
    run = runner(repo, lock, agent=orch).start(Cycle.ADHOC, "A", "clean3", background=False)
    assert fake.calls == [] and repo.list_agent_runs() == []
    assert repo.get_run(run.id).status == RunStatus.COMPLETED


def test_a_luna_crash_cannot_fail_the_run(repo, lock) -> None:
    class Exploding:
        def process(self, run_id, events):
            raise RuntimeError("model gateway on fire")

    make_source(repo, "clean4", rss_url=None)
    run = runner(repo, lock, agent=Exploding()).start(Cycle.ADHOC, "A", "clean4", background=False)
    assert repo.get_run(run.id).status == RunStatus.COMPLETED  # items flowed, run closed normally
    assert not lock.held()
