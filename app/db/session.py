"""Database engine and sessions. SQLite for the POC; any SQLAlchemy URL (PostgreSQL) by config."""

import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

DEFAULT_DATABASE_URL = "sqlite:///data/reg_monitor.db"
BUSY_TIMEOUT_MS = 30_000


ROOT = Path(__file__).resolve().parents[2]


def database_url() -> str:
    """DATABASE_URL from the environment (see .env.example), else the local SQLite file.

    A relative SQLite path is taken from the repository root, not the current folder, so the app,
    Alembic and the crawler (which runs inside crawler/) all open the same file.
    """
    return _absolute_sqlite(os.environ.get("DATABASE_URL", "").strip() or DEFAULT_DATABASE_URL)


def _absolute_sqlite(url: str) -> str:
    parsed = make_url(url)
    name = parsed.database
    if parsed.get_backend_name() != "sqlite" or name in (None, "", ":memory:"):
        return url
    if Path(name).expanduser().is_absolute():
        return url
    return f"sqlite:///{(ROOT / name).as_posix()}"


def _is_sqlite(url: str) -> bool:
    return make_url(url).get_backend_name() == "sqlite"


def ensure_sqlite_folder(url: str) -> None:
    """Create the folder for a SQLite file (the data/ folder is not in git)."""
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in (None, "", ":memory:"):
        Path(parsed.database).expanduser().parent.mkdir(parents=True, exist_ok=True)


def _configure_sqlite(dbapi_connection: sqlite3.Connection, _record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")  # SQLite ignores foreign keys unless asked
    cursor.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")  # wait for a writer, do not fail
    cursor.close()


def make_engine(url: str | None = None, **kwargs: Any) -> Engine:
    """Engine for `url` (default: DATABASE_URL). Foreign keys are enforced on SQLite too."""
    url = _absolute_sqlite(url) if url else database_url()
    if _is_sqlite(url):
        ensure_sqlite_folder(url)
        engine = create_engine(url, **kwargs)
        event.listen(engine, "connect", _configure_sqlite)
        return engine
    return create_engine(url, pool_pre_ping=True, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


@contextmanager
def session_scope(factory: sessionmaker[Session]) -> Iterator[Session]:
    """One transaction: commit if the block finishes, roll back if it raises."""
    session = factory()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()
