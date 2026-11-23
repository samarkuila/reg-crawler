"""The one place the crawler gets its repository (DB access stays in app.repositories)."""

import os
from typing import Any

from app.repositories.base import Repository

_default: Repository | None = None


def get_repository(settings: Any = None) -> Repository:
    """Repository for this crawl: one injected through the settings (tests), else the SQL backend.

    A Scrapy subprocess has no use for the in-memory backend, so REPO_BACKEND defaults to `sql`.
    """
    global _default
    injected = settings.get("REGCRAWLER_REPOSITORY") if settings is not None else None
    if injected is not None:
        return injected
    if _default is None:
        from app.repositories.factory import make_repository

        _default = make_repository(os.environ.get("REPO_BACKEND", "sql"))
    return _default
