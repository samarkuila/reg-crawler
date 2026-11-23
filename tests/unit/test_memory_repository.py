"""Checks that belong to the factory and the in-memory repository only.

The behaviour both backends share (protocol, seed data, rollback, audit, copies) is in
tests/contract/test_repository_contract.py, which runs on the in-memory AND the SQL repository.
"""

import pytest

from app.repositories import seed
from app.repositories.factory import make_repository
from app.repositories.memory import MemoryRepository
from app.repositories.sql import SqlRepository
from tests.conftest import make_backend


def test_factory_backends(monkeypatch, tmp_path, sql_template) -> None:
    assert isinstance(make_repository("memory"), MemoryRepository)
    monkeypatch.setenv("REPO_BACKEND", "memory")
    assert isinstance(make_repository(), MemoryRepository)
    with pytest.raises(ValueError):
        make_repository("mongo")
    # SQL: an empty database is refused with a pointer to the migrate command
    empty = tmp_path / "empty.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{empty.as_posix()}")
    with pytest.raises(RuntimeError, match="migrate"):
        make_repository("sql")
    # SQL: a migrated database works, selected either by name or by REPO_BACKEND
    _repo, url, _files = make_backend("sql", tmp_path, sql_template)
    monkeypatch.setenv("DATABASE_URL", url)
    assert isinstance(make_repository("sql"), SqlRepository)
    monkeypatch.setenv("REPO_BACKEND", "sql")
    assert isinstance(make_repository(), SqlRepository)


def test_each_factory_call_returns_independent_state() -> None:
    a, b = make_repository("memory"), make_repository("memory")
    a.add_manual_check("chk-2026-11-20-eod-egazette-html", "Divya G.", "ok", "Divya G.")
    assert b.get_manual_check("chk-2026-11-20-eod-egazette-html") is None


def test_fingerprint_rule_uses_ref_no_else_url_never_the_date() -> None:
    with_ref = seed.fingerprint("rbi", "RBI/2026-27/118", "https://x/1")
    assert with_ref == seed.fingerprint("rbi", "RBI/2026-27/118", "https://x/2")  # url ignored
    assert seed.fingerprint("rbi", None, "https://x/1") != seed.fingerprint(
        "rbi", None, "https://x/2"
    )
    assert with_ref != seed.fingerprint("trai", "RBI/2026-27/118", "https://x/1")
