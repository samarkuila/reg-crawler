"""Business logic. Routers call services; services call the Repository protocol."""

from dataclasses import dataclass

from app.repositories.base import Repository
from app.services.audit import AuditService
from app.services.config import ConfigService
from app.services.cycle_export import CycleExportService
from app.services.daily_update import DailyUpdateService
from app.services.email_render import EmailRenderService
from app.services.health import HealthService
from app.services.history import HistoryService
from app.services.review import ReviewService
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


def build_services(repo: Repository, source_tester: SourceTester | None = None) -> Services:
    review = ReviewService(repo)
    return Services(
        repo=repo,
        review=review,
        cycle_export=CycleExportService(repo, review),
        health=HealthService(repo, review),
        history=HistoryService(repo),
        config=ConfigService(repo, source_tester),
        audit=AuditService(repo),
        daily_update=DailyUpdateService(repo, EmailRenderService()),
        users=UserService(repo),
    )
