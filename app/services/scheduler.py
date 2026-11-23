"""Scheduler: starts runs at the times in the settings (FR-05). One worker only.

    10:00 (morning) and 19:30 (EOD), Mon-Fri, not on a holiday      -> a run of every source
    08:00 ... 20:00 every hour on the same days                     -> a run of the URGENT sources

Times, working days and holidays come from the `run-times`, `working-days` and `holidays` settings
(IST). When one of them is approved, `reload()` rebuilds the jobs, so a change takes effect without
restarting. The hourly urgent runs follow the same working days and holidays as the others.

A scheduled run that cannot start because another run is going is skipped and recorded as an alert.
On start-up, if today's morning or EOD slot has passed with no run, one catch-up run is started and
labelled with the missed cycle (system design §3.2).

APScheduler's BackgroundScheduler runs in this process; start it only in a single-worker app.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from app.repositories.base import Repository
from app.schemas.entities import Alert, now_ist
from app.schemas.enums import AlertKind, Cycle
from app.services.crawl_runner import CrawlRunner
from app.services.errors import ConflictError, DomainError

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")
SETTING_KEYS = ("run-times", "working-days", "holidays")
DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]
CATCH_UP_GRACE = timedelta(minutes=15)  # a slot is "missed" this long after its time
DEFAULTS = {
    "morning": "10:00",
    "eod": "19:30",
    "urgent_hourly_from": "08:00",
    "urgent_hourly_to": "20:00",
}


def _hm(text: str) -> tuple[int, int]:
    hour, minute = text.strip().split(":")
    return int(hour), int(minute)


def parse_date(text: str) -> date | None:
    for fmt in ("%d.%m.%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(text.strip(), fmt).date()
        except ValueError:
            continue
    return None


@dataclass(frozen=True)
class ScheduleConfig:
    morning: tuple[int, int]
    eod: tuple[int, int]
    urgent_from: tuple[int, int]
    urgent_to: tuple[int, int]
    days: tuple[str, ...]
    holidays: frozenset[date]

    def is_working_day(self, day: date) -> bool:
        return DAYS[day.weekday()] in self.days and day not in self.holidays


@dataclass(frozen=True)
class JobSpec:
    id: str
    cycle: Cycle
    hour: str  # cron hour field
    minute: int
    urgent_only: bool = False


def load_config(repo: Repository) -> ScheduleConfig:
    def value(key: str, default):  # type: ignore[no-untyped-def]
        setting = repo.get_setting(key)
        return setting.value if setting is not None and setting.value is not None else default

    times = {**DEFAULTS, **(value("run-times", {}) or {})}
    days = tuple(str(d).upper()[:3] for d in value("working-days", DAYS[:5]))
    holidays = {d for d in (parse_date(str(h)) for h in value("holidays", [])) if d is not None}
    return ScheduleConfig(
        morning=_hm(times["morning"]),
        eod=_hm(times["eod"]),
        urgent_from=_hm(times["urgent_hourly_from"]),
        urgent_to=_hm(times["urgent_hourly_to"]),
        days=days,
        holidays=frozenset(holidays),
    )


def build_specs(cfg: ScheduleConfig) -> list[JobSpec]:
    specs = [
        JobSpec("morning", Cycle.MORNING, str(cfg.morning[0]), cfg.morning[1]),
        JobSpec("eod", Cycle.EOD, str(cfg.eod[0]), cfg.eod[1]),
    ]
    first, last = cfg.urgent_from[0], cfg.urgent_to[0]
    if last >= first:
        specs.append(
            JobSpec("urgent-hourly", Cycle.URGENT, f"{first}-{last}", cfg.urgent_from[1], True)
        )
    return specs


class SchedulerService:
    def __init__(
        self,
        repo: Repository,
        runner: CrawlRunner,
        scheduler: BackgroundScheduler | None = None,
    ) -> None:
        self._repo = repo
        self._runner = runner
        self._scheduler = scheduler or BackgroundScheduler(
            timezone=IST,
            job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 300},
        )
        self.config = load_config(repo)

    # --- lifecycle ---------------------------------------------------------------------------

    def start(self) -> None:
        self.reload()
        if not self._scheduler.running:
            self._scheduler.start()
        self.catch_up()

    def shutdown(self) -> None:
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)

    def reload(self) -> None:
        """Rebuild every job from the current settings (called when a schedule setting changes)."""
        self.config = load_config(self._repo)
        for job in self._scheduler.get_jobs():
            job.remove()
        days = ",".join(d.lower() for d in self.config.days) or "mon-fri"
        for spec in build_specs(self.config):
            self._scheduler.add_job(
                self.fire,
                CronTrigger(day_of_week=days, hour=spec.hour, minute=spec.minute, timezone=IST),
                args=[spec],
                id=spec.id,
                name=f"{spec.cycle.value.title()} run",
                replace_existing=True,
            )

    def on_setting_approved(self, key: str) -> None:
        if key in SETTING_KEYS:
            self.reload()

    def jobs(self) -> list[tuple[str, str]]:
        """(job id, trigger text) for the jobs now scheduled; used by tests and the status page."""
        return [(j.id, str(j.trigger)) for j in self._scheduler.get_jobs()]

    # --- firing ------------------------------------------------------------------------------

    def fire(self, spec: JobSpec, now: datetime | None = None) -> None:
        """Run one scheduled job: working days only, never overlapping another run."""
        today = (now or now_ist()).date()
        if not self.config.is_working_day(today):
            log.info("Skipping %s: %s is not a working day", spec.id, today)
            return
        try:
            self._runner.start(spec.cycle, "scheduler", urgent_only=spec.urgent_only)
        except ConflictError as exc:
            self._skipped(spec, exc.message)
        except DomainError as exc:
            if spec.urgent_only and "no active sources" in exc.message:
                return  # no URGENT source is set up: nothing to run, nothing to report
            self._skipped(spec, exc.message)

    def _skipped(self, spec: JobSpec, why: str) -> None:
        self._repo.add_alerts(
            [
                Alert(
                    id=f"alr-skip-{spec.id}-{now_ist():%Y%m%d%H%M}",
                    kind=AlertKind.RUN_SKIPPED,
                    severity="warning",
                    message=f"The scheduled {spec.cycle.value.lower()} run did not start. {why}",
                    created_at=now_ist(),
                )
            ],
            "scheduler",
        )

    def catch_up(self, now: datetime | None = None) -> Cycle | None:
        """If a regular slot passed today with no run, start one catch-up run for the latest one."""
        now = now or now_ist()
        if not self.config.is_working_day(now.date()):
            return None
        todays = {r.cycle for r in self._repo.list_runs(now.date())}
        slots = [(Cycle.MORNING, self.config.morning), (Cycle.EOD, self.config.eod)]
        missed = [
            cycle
            for cycle, (h, m) in slots
            if cycle not in todays
            and now >= now.replace(hour=h, minute=m, second=0, microsecond=0) + CATCH_UP_GRACE
        ]
        if not missed:
            return None
        cycle = missed[-1]
        try:
            run = self._runner.start(cycle, "scheduler (catch-up)")
        except DomainError as exc:
            self._skipped(JobSpec("catch-up", cycle, "0", 0), exc.message)
            return None
        self._repo.add_alerts(
            [
                Alert(
                    id=f"alr-missed-{run.id}",
                    run_id=run.id,
                    kind=AlertKind.RUN_MISSED,
                    severity="warning",
                    message=(
                        f"The {cycle.value.lower()} run did not happen at its time. "
                        "A catch-up run was started."
                    ),
                    created_at=now_ist(),
                )
            ],
            "scheduler",
        )
        return cycle
