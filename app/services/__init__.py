"""Business logic. Routers call services; services call the Repository protocol."""

from dataclasses import dataclass

from app.repositories.base import Repository
from app.services.alerts import AlertService
from app.services.audit import AuditService
from app.services.config import ConfigService
from app.services.crawl_runner import CrawlRunner, SubprocessLauncher
from app.services.cycle_export import CycleExportService
from app.services.daily_update import DailyUpdateService
from app.services.documents import DocumentService, SubprocessDocumentFetcher
from app.services.email_render import EmailRenderService
from app.services.health import HealthService
from app.services.history import HistoryService
from app.services.review import ReviewService
from app.services.scheduler import SchedulerService
from app.services.source_test import SourceTester
from app.services.users import UserService


@dataclass
class Services:
    repo: Repository
    review: ReviewService
    config: ConfigService
    audit: AuditService
    daily_update: DailyUpdateService
    cycle_export: CycleExportService
    health: HealthService
    history: HistoryService
    users: UserService
    crawl: CrawlRunner
    scheduler: SchedulerService
    alerts: AlertService


def build_services(
    repo: Repository,
    source_tester: SourceTester | None = None,
    crawl_runner: CrawlRunner | None = None,
) -> Services:
    review = ReviewService(repo)
    runner = crawl_runner or CrawlRunner(
        repo, SubprocessLauncher(), DocumentService(repo, SubprocessDocumentFetcher())
    )
    scheduler = SchedulerService(repo, runner)
    config = ConfigService(repo, source_tester)
    config.on_setting_approved = scheduler.on_setting_approved  # a schedule change takes effect
    return Services(
        repo=repo,
        review=review,
        cycle_export=CycleExportService(repo, review),
        health=HealthService(repo, review),
        history=HistoryService(repo),
        config=config,
        audit=AuditService(repo),
        daily_update=DailyUpdateService(repo, EmailRenderService()),
        users=UserService(repo),
        crawl=runner,
        scheduler=scheduler,
        alerts=AlertService(repo),
    )
