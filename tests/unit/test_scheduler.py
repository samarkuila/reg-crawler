"""Scheduler: jobs from settings, working days and holidays, reload on change, catch-up."""

from datetime import date, datetime

import pytest

from app.repositories import seed
from app.repositories.memory import MemoryRepository
from app.schemas.enums import AlertKind, Cycle
from app.services.config import ConfigService
from app.services.errors import ConflictError, InvalidError
from app.services.scheduler import (
    IST,
    JobSpec,
    SchedulerService,
    build_specs,
    load_config,
    parse_date,
)

MON, SAT = date(2026, 11, 23), date(2026, 11, 28)


class FakeRunner:
    def __init__(self, error=None) -> None:
        self.started: list[tuple] = []
        self.error = error

    def start(self, cycle, triggered_by, source_id=None, *, urgent_only=False, background=True):
        if self.error:
            raise self.error
        self.started.append((cycle, triggered_by, urgent_only))
        return type("Run", (), {"id": f"run-{len(self.started)}"})()


@pytest.fixture
def repo():
    return seed.load_seed(MemoryRepository())


def at(day: date, hour: int, minute: int = 0) -> datetime:
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)


def set_setting(repo, key, value):
    repo.propose_setting(key, value, "George K.", "George K.")
    repo.approve_setting(key, "Divya G.", "Divya G.")


# --- settings to jobs -----------------------------------------------------------------------------


def test_the_default_settings_give_the_three_jobs(repo) -> None:
    cfg = load_config(repo)
    assert (cfg.morning, cfg.eod, cfg.urgent_from, cfg.urgent_to) == (
        (10, 0), (19, 30), (8, 0), (20, 0),
    )  # fmt: skip
    assert cfg.days == ("MON", "TUE", "WED", "THU", "FRI")
    specs = {s.id: s for s in build_specs(cfg)}
    assert (specs["morning"].hour, specs["morning"].minute) == ("10", 0)
    assert (specs["eod"].hour, specs["eod"].minute) == ("19", 30)
    assert specs["urgent-hourly"].hour == "8-20" and specs["urgent-hourly"].urgent_only
    assert specs["urgent-hourly"].cycle == Cycle.URGENT


def test_reload_schedules_the_jobs_in_ist_on_working_days(repo) -> None:
    service = SchedulerService(repo, FakeRunner())
    service.reload()
    jobs = dict(service.jobs())
    assert set(jobs) == {"morning", "eod", "urgent-hourly"}
    assert "day_of_week='mon,tue,wed,thu,fri'" in jobs["morning"]
    assert "hour='10'" in jobs["morning"] and "minute='0'" in jobs["morning"]
    assert "hour='19'" in jobs["eod"] and "minute='30'" in jobs["eod"]
    assert "hour='8-20'" in jobs["urgent-hourly"]


def test_an_approved_schedule_change_reloads_the_jobs(repo) -> None:
    service = SchedulerService(repo, FakeRunner())
    service.reload()
    set_setting(
        repo,
        "run-times",
        {
            "morning": "09:15",
            "eod": "18:00",
            "urgent_hourly_from": "07:00",
            "urgent_hourly_to": "21:00",
        },
    )
    service.on_setting_approved("run-times")
    jobs = dict(service.jobs())
    assert "hour='9'" in jobs["morning"] and "minute='15'" in jobs["morning"]
    assert "hour='18'" in jobs["eod"] and "hour='7-21'" in jobs["urgent-hourly"]
    assert len(jobs) == 3  # replaced, not added to


def test_other_settings_do_not_reload(repo) -> None:
    service = SchedulerService(repo, FakeRunner())
    service.reload()
    before = service.config
    set_setting(repo, "lookback-days", 5)
    service.on_setting_approved("lookback-days")
    assert service.config is before


def test_the_config_service_tells_the_scheduler_when_a_setting_is_approved(repo) -> None:
    seen = []
    config = ConfigService(repo)
    config.on_setting_approved = seen.append
    admin = next(u for u in repo.list_users() if u.role.value == "admin")
    approver = next(u for u in repo.list_users() if u.role.value == "approver")
    repo.propose_setting("working-days", ["MON", "TUE"], admin.name, admin.name)
    config.approve_setting(approver, "working-days")
    assert seen == ["working-days"]


def test_holidays_and_working_days_are_read_from_the_settings(repo) -> None:
    set_setting(repo, "holidays", ["26.11.2026", "2026-12-25", "not a date"])
    set_setting(repo, "working-days", ["MON", "TUE", "WED", "THU", "FRI", "SAT"])
    cfg = load_config(repo)
    assert date(2026, 11, 26) in cfg.holidays and date(2026, 12, 25) in cfg.holidays
    assert cfg.is_working_day(SAT) and not cfg.is_working_day(date(2026, 11, 26))
    assert parse_date("26.11.2026") == date(2026, 11, 26) and parse_date("x") is None


# --- firing ---------------------------------------------------------------------------------------


def test_a_job_starts_a_run_on_a_working_day(repo) -> None:
    runner = FakeRunner()
    service = SchedulerService(repo, runner)
    service.fire(JobSpec("morning", Cycle.MORNING, "10", 0), now=at(MON, 10))
    service.fire(JobSpec("urgent-hourly", Cycle.URGENT, "8-20", 0, True), now=at(MON, 11))
    assert runner.started == [
        (Cycle.MORNING, "scheduler", False),
        (Cycle.URGENT, "scheduler", True),
    ]


@pytest.mark.parametrize("day", [SAT, date(2026, 11, 29)])
def test_nothing_runs_at_the_weekend(repo, day) -> None:
    runner = FakeRunner()
    SchedulerService(repo, runner).fire(JobSpec("eod", Cycle.EOD, "19", 30), now=at(day, 19, 30))
    assert runner.started == []


def test_nothing_runs_on_a_holiday(repo) -> None:
    set_setting(repo, "holidays", ["23.11.2026"])
    runner = FakeRunner()
    SchedulerService(repo, runner).fire(JobSpec("morning", Cycle.MORNING, "10", 0), now=at(MON, 10))
    assert runner.started == []


def test_a_scheduled_run_that_cannot_start_because_another_is_going_is_recorded(repo) -> None:
    service = SchedulerService(repo, FakeRunner(ConflictError("A crawl is already running.")))
    service.fire(JobSpec("eod", Cycle.EOD, "19", 30), now=at(MON, 19, 30))
    (alert,) = [a for a in repo.list_alerts() if a.kind == AlertKind.RUN_SKIPPED]
    assert (
        alert.run_id is None
        and "did not start" in alert.message
        and "already running" in alert.message
    )


def test_urgent_runs_with_no_urgent_source_are_silent(repo) -> None:
    error = InvalidError("There are no active sources to crawl.")
    SchedulerService(repo, FakeRunner(error)).fire(
        JobSpec("urgent-hourly", Cycle.URGENT, "8-20", 0, True), now=at(MON, 9)
    )
    assert [a for a in repo.list_alerts() if a.kind == AlertKind.RUN_SKIPPED] == []


# --- catch-up -------------------------------------------------------------------------------------


def test_a_missed_morning_run_is_caught_up_and_labelled_with_its_cycle(repo) -> None:
    runner = FakeRunner()
    service = SchedulerService(repo, runner)
    assert service.catch_up(at(MON, 12)) == Cycle.MORNING
    assert runner.started == [(Cycle.MORNING, "scheduler (catch-up)", False)]
    missed = [a for a in repo.list_alerts() if a.kind == AlertKind.RUN_MISSED]
    assert len(missed) == 1 and "morning run did not happen" in missed[0].message


def test_only_the_latest_missed_slot_is_caught_up(repo) -> None:
    runner = FakeRunner()
    assert SchedulerService(repo, runner).catch_up(at(MON, 21)) == Cycle.EOD
    assert len(runner.started) == 1


def test_no_catch_up_when_the_slot_has_a_run_or_has_not_come_yet(repo) -> None:
    from app.schemas.entities import now_ist

    runner = FakeRunner()
    service = SchedulerService(repo, runner)
    assert service.catch_up(at(MON, 9, 59)) is None  # before the morning slot
    assert service.catch_up(at(MON, 10, 5)) is None  # inside the grace period
    repo.create_run(Cycle.MORNING, MON, "scheduler", "scheduler")
    assert service.catch_up(at(MON, 11)) is None  # the morning run exists
    assert now_ist() and runner.started == []


def test_no_catch_up_on_a_weekend_or_holiday(repo) -> None:
    set_setting(repo, "holidays", ["23.11.2026"])
    runner = FakeRunner()
    service = SchedulerService(repo, runner)
    assert service.catch_up(at(SAT, 12)) is None and service.catch_up(at(MON, 12)) is None
    assert runner.started == []


def test_a_catch_up_that_cannot_start_is_recorded_not_raised(repo) -> None:
    service = SchedulerService(repo, FakeRunner(ConflictError("already running")))
    assert service.catch_up(at(MON, 12)) is None
    assert any(a.kind == AlertKind.RUN_SKIPPED for a in repo.list_alerts())
