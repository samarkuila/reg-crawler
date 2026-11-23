"""Business logic. Routers call services; services call the Repository protocol."""

from dataclasses import dataclass

from app.repositories.base import Repository
from app.services.audit import AuditService
from app.services.config import ConfigService
from app.services.daily_update import DailyUpdateService
from app.services.email_render import EmailRenderService
from app.services.review import ReviewService
from app.services.users import UserService


@dataclass
class Services:
    repo: Repository
    review: ReviewService
    config: ConfigService
    audit: AuditService
    daily_update: DailyUpdateService
    users: UserService


def build_services(repo: Repository) -> Services:
    return Services(
        repo=repo,
        review=ReviewService(repo),
        config=ConfigService(repo),
        audit=AuditService(repo),
        daily_update=DailyUpdateService(repo, EmailRenderService()),
        users=UserService(repo),
    )
