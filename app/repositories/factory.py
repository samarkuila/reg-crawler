"""Choose the repository backend from REPO_BACKEND (memory | sql)."""

import os

from sqlalchemy import inspect

from app.repositories.base import Repository


def make_repository(backend: str | None = None) -> Repository:
    name = (backend or os.environ.get("REPO_BACKEND", "memory")).strip().lower()
    if name == "memory":
        from app.repositories.memory import MemoryRepository
        from app.repositories.seed import load_seed

        return load_seed(MemoryRepository())
    if name == "sql":
        from app.db.session import make_engine
        from app.repositories.sql import SqlRepository

        engine = make_engine()
        if "source" not in inspect(engine).get_table_names():
            raise RuntimeError(
                "The database has no tables yet. Run 'tasks.ps1 migrate' first "
                "(and 'tasks.ps1 seed-demo' for demo data)."
            )
        return SqlRepository(engine)
    raise ValueError(f"Unknown REPO_BACKEND {name!r}; use 'memory' or 'sql'")
