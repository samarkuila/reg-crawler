"""Migration 0010: "browser" stops being a way of reading and becomes a way of fetching.

Old sources and old stored versions must still load, and no engine is ever switched on for a
source whose person did not choose the old "Browser-rendered page".
"""

import pytest
import sqlalchemy as sa
from alembic import command

from app.db.session import make_engine
from app.repositories.sql import SqlRepository
from app.schemas.entities import Source, SourceVersion, now_ist
from app.schemas.enums import FetchEngine, SourceType, VersionStatus
from tests.conftest import _alembic

ROWS = [
    # id, type, requires_js
    ("old-browser", "BROWSER", 1),  # the old "Browser-rendered page"
    ("old-browser-no-flag", "BROWSER", 0),
    ("stale-html", "HTML_LIST", 1),  # a flag left over after the dropdown was changed
    ("stale-rss", "RSS", 1),
    ("plain-html", "HTML_LIST", 0),
    ("data-file", "STRUCTURED_DATA", 0),
]


@pytest.fixture
def migrated(tmp_path):
    url = f"sqlite:///{(tmp_path / 'm.db').as_posix()}"
    cfg = _alembic(url)
    command.upgrade(cfg, "0009")
    engine = make_engine(url)
    with engine.begin() as conn:
        for position, (sid, kind, js) in enumerate(ROWS):
            conn.execute(
                sa.text(
                    "INSERT INTO source (id, authority, page_name, type, url, adapter_config, "
                    "include_keywords, exclude_keywords, priority, requires_js, active, "
                    "current_version, planned, position) VALUES (:id, 'A', :id, :type, "
                    "'https://x.example/', '{}', '[]', '[]', 'NORMAL', :js, 1, 1, 0, :pos)"
                ),
                {"id": sid, "type": kind, "js": js, "pos": position},
            )
    engine.dispose()
    command.upgrade(cfg, "head")
    engine = make_engine(url)
    yield SqlRepository(engine, files_root=tmp_path / "files"), engine
    engine.dispose()


def test_old_browser_sources_become_html_listings_fetched_with_the_browser(migrated) -> None:
    repo, _ = migrated
    for sid in ("old-browser", "old-browser-no-flag"):
        s = repo.get_source(sid)
        assert (s.type, s.fetch_engine) == (SourceType.HTML_LIST, FetchEngine.BROWSER)
        assert s.wait_for_selector is None and s.wait_timeout_s == 15
        assert s.uses_browser


def test_every_other_source_is_fetched_with_the_standard_engine(migrated) -> None:
    repo, _ = migrated
    for sid in ("stale-html", "stale-rss", "plain-html", "data-file"):
        s = repo.get_source(sid)
        assert s.fetch_engine == FetchEngine.HTTP and not s.uses_browser
    assert repo.get_source("stale-rss").type == SourceType.RSS  # reading is untouched
    assert repo.get_source("data-file").type == SourceType.STRUCTURED_DATA


def test_the_old_flag_is_cleared_and_each_conversion_is_audited(migrated) -> None:
    repo, engine = migrated
    with engine.connect() as conn:
        assert conn.scalar(sa.text("SELECT COUNT(*) FROM source WHERE requires_js")) == 0
        events = conn.execute(
            sa.text("SELECT entity_id, actor, action FROM audit_event ORDER BY entity_id")
        ).all()
    converted = {"old-browser", "old-browser-no-flag", "stale-html", "stale-rss"}
    assert {e[0] for e in events} == converted
    assert all(e[1] == "migration-0010" and e[2] == "source.fetch_engine_migrated" for e in events)


def test_audit_events_stay_append_only(migrated) -> None:
    _, engine = migrated
    with engine.begin() as conn, pytest.raises(sa.exc.DBAPIError):
        conn.execute(sa.text("UPDATE audit_event SET actor = 'x'"))


def test_old_stored_versions_still_load() -> None:
    old = {
        "id": "x",
        "authority": "A",
        "page_name": "P",
        "type": "BROWSER",
        "url": "https://x.example/",
        "requires_js": True,
    }
    s = Source.model_validate(old)
    assert (s.type, s.fetch_engine) == (SourceType.HTML_LIST, FetchEngine.BROWSER)
    assert not hasattr(s, "requires_js") and "requires_js" not in s.model_dump()
    # A stale flag on any other type is dropped; it never becomes a browser fetch.
    stale = Source.model_validate({**old, "type": "RSS"})
    assert stale.fetch_engine == FetchEngine.HTTP


def test_an_old_pending_version_can_still_be_approved(migrated) -> None:
    repo, _ = migrated
    version = SourceVersion(
        id="srcv-old-1",
        source_id="plain-html",
        version=1,
        before={},
        after={"type": "BROWSER", "requires_js": True, "page_name": "Old style change"},
        status=VersionStatus.PENDING,
        requested_by="Someone",
        ts=now_ist(),
    )
    from app.db import models as m

    with repo._tx() as session:
        session.add(m.SourceVersion(**version.model_dump()))
    repo.approve_source_version("plain-html", 1, "Approver", "Approver")
    s = repo.get_source("plain-html")
    assert (s.type, s.fetch_engine, s.page_name) == (
        SourceType.HTML_LIST,
        FetchEngine.BROWSER,
        "Old style change",
    )
