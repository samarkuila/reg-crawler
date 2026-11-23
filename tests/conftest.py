"""Shared test setup: a seeded SQLite database is built once, then copied for each test.

Tests that run against both repository backends use `make_backend(name, tmp_path, monkeypatch)`.
Nothing here touches the network or the real data/ folder.
"""

import shutil
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config

from app.db.session import make_engine
from app.repositories import seed
from app.repositories.memory import MemoryRepository
from app.repositories.sql import SqlRepository

ROOT = Path(__file__).resolve().parents[1]
BACKENDS = ["memory", "sql"]


def _alembic(url: str) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(scope="session")
def sql_template(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A migrated database with the demo data, plus its stored files. Copied, never modified."""
    folder = tmp_path_factory.mktemp("sql-template")
    url = f"sqlite:///{(folder / 'reg_monitor.db').as_posix()}"
    command.upgrade(_alembic(url), "head")
    engine = make_engine(url)
    seed.load_seed(SqlRepository(engine, files_root=folder / "files"))
    engine.dispose()
    return folder


def make_backend(name: str, tmp_path: Path, template: Path | None):
    """(repository, database_url, files_root) for a fresh, seeded backend."""
    if name == "memory":
        return seed.load_seed(MemoryRepository()), None, None
    folder = tmp_path / "sql"
    shutil.copytree(template, folder)
    url = f"sqlite:///{(folder / 'reg_monitor.db').as_posix()}"
    return SqlRepository(make_engine(url), files_root=folder / "files"), url, folder / "files"
