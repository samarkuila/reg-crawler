"""Choose the repository backend from REPO_BACKEND (memory | sql)."""

import os

from app.repositories.base import Repository


def make_repository(backend: str | None = None) -> Repository:
    name = (backend or os.environ.get("REPO_BACKEND", "memory")).strip().lower()
    if name == "memory":
        from app.repositories.memory import MemoryRepository
        from app.repositories.seed import load_seed

        return load_seed(MemoryRepository())
    if name == "sql":
        raise NotImplementedError("The SQL repository arrives in Prompt 7 (M2)")
    raise ValueError(f"Unknown REPO_BACKEND {name!r}; use 'memory' or 'sql'")
