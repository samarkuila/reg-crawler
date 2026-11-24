"""First-crawl window and baseline: what a source's first crawl stores, what it only remembers, and
how later crawls tell old-and-known from new. Offline; both repositories."""

from datetime import date

import pytest

from app.schemas.api import ItemFilter
from app.schemas.entities import AppUser
from app.schemas.enums import (
    BaselineStatus,
    ChangeType,
    CheckStatus,
    FirstRunMode,
    ItemOrigin,
    SeenReason,
    UserRole,
)
from app.services.config import ConfigService
from app.services.detection import DetectionService
from app.services.errors import ConflictError, ForbiddenError
from app.services.first_run import FirstRunConfig, window_for, window_start
from app.services.review import ReviewService
from tests.conftest import BACKENDS, make_backend
from tests.detection_helpers import crawl, make_source, row

SRC = "fr-source"
TODAY = date(2026, 11, 24)
CFG = FirstRunConfig()  # current_month, min 7 days, 20 undated

THIS_MONTH_A = (date(2026, 11, 20), "RBI/2026-27/60")
THIS_MONTH_B = (date(2026, 11, 3), "RBI/2026-27/55")
LAST_MONTH = (date(2026, 10, 15), "RBI/2026-27/40")
OLD_2025 = (date(2025, 6, 30), "RBI/2025-26/12")


def page_rows() -> list[dict]:
    return [row(f"Notification {ref}", ref, day) for day, ref in (
        THIS_MONTH_A, THIS_MONTH_B, LAST_MONTH, OLD_2025)]  # fmt: skip


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path, sql_template):
    repository, _url, _files = make_backend(request.param, tmp_path, sql_template)
    make_source(repository, SRC, baseline_status=BaselineStatus.NOT_BASELINED)
    return repository


def detect(repo, run, cfg: FirstRunConfig = CFG, today: date = TODAY):
    return DetectionService(repo, cfg, today=lambda: today).process(run.id)


def mine(repo):
    return [i for i in repo.list_items(ItemFilter()) if i.source_id == SRC]


def check_of(repo, run):
    return next(c for c in repo.list_source_checks(run.id) if c.source_id == SRC)


# --- the window --------------------------------------------------------------------------------


def test_window_start_examples() -> None:
    kolkata = "Asia/Kolkata"
    assert window_start(date(2026, 11, 24), "current_month", 30, 7, kolkata) == date(2026, 11, 1)
    # early in the month the minimum look-back wins, so nothing recent is lost
    assert window_start(date(2026, 12, 2), "current_month", 30, 7, kolkata) == date(2026, 11, 25)
    assert window_start(date(2026, 11, 24), "last_n_days", 30, 7, kolkata) == date(2026, 10, 25)
    assert window_start(date(2026, 11, 24), "all", 30, 7, kolkata) is None


def test_a_source_can_override_the_window() -> None:
    plain = make_source_entity()
    assert window_for(plain, CFG, TODAY).start == date(2026, 11, 1)
    forced = make_source_entity(first_run_mode=FirstRunMode.LAST_N_DAYS, first_run_days=10)
    assert window_for(forced, CFG, TODAY).start == date(2026, 11, 14)
    assert window_for(make_source_entity(first_run_mode=FirstRunMode.ALL), CFG, TODAY).start is None


def make_source_entity(**updates):
    from app.repositories.memory import MemoryRepository
    from app.repositories.seed import sources

    base = next(s for s in sources() if s.active)
    del MemoryRepository
    return base.model_copy(update=updates)


# --- first crawl ---------------------------------------------------------------------------------


def test_first_crawl_stores_only_the_window_and_remembers_the_rest(repo) -> None:
    run = crawl(repo, SRC, TODAY, html=page_rows())
    result = detect(repo, run)

    items = mine(repo)
    assert sorted(i.ref_no for i in items) == sorted([THIS_MONTH_A[1], THIS_MONTH_B[1]])
    assert {i.origin for i in items} == {ItemOrigin.BASELINE}
    assert {e.change for e in result.events} == {ChangeType.NEW}

    seen = repo.list_seen_fingerprints(SRC)  # recorded, never dropped
    assert len(seen) == 2 and {s.reason for s in seen} == {SeenReason.OUT_OF_WINDOW}
    assert {s.published_date for s in seen} == {LAST_MONTH[0], OLD_2025[0]}
    assert all(s.first_seen_run_id == run.id for s in seen)

    source = repo.get_source(SRC)
    assert source.baseline_status == BaselineStatus.BASELINED
    assert source.baseline_window_start == date(2026, 11, 1) and source.baseline_at

    event = next(e for e in repo.list_audit() if e.action == "BASELINE_COMPLETED")
    assert event.entity_id == SRC
    assert event.after["detail"]["items_stored"] == 2 and event.after["detail"]["seen_only"] == 2
    assert event.after["detail"]["window_start"] == "2026-11-01"
    assert event.before["baseline_status"] == "NOT_BASELINED"

    stats = check_of(repo, run).stats  # the run log counts what was left out (rule 3)
    assert stats["rows_parsed"] == 4 and stats["baseline_items"] == 2 and stats["seen_only"] == 2
    assert stats["window_start"] == "2026-11-01"


def test_an_undated_source_keeps_only_the_first_n_rows(repo) -> None:
    rows = [row(f"Undated item {k:02d}", f"UND/{k}", None) for k in range(12)]
    run = crawl(repo, SRC, TODAY, html=rows)
    detect(repo, run, FirstRunConfig(undated_max_items=5))
    kept = {i.ref_no for i in mine(repo)}
    assert kept == {f"UND/{k}" for k in range(5)}  # the first five, in page order
    seen = repo.list_seen_fingerprints(SRC)
    assert len(seen) == 7 and {s.reason for s in seen} == {SeenReason.UNDATED_BEYOND_LIMIT}


@pytest.mark.parametrize(
    "status", [CheckStatus.BLOCKED, CheckStatus.FAILED, CheckStatus.ZERO_ITEMS]
)
def test_a_blocked_failed_or_empty_first_run_is_not_baselined(repo, status) -> None:
    run = crawl(repo, SRC, TODAY, html=[] if status == CheckStatus.ZERO_ITEMS else None,
                html_status=status)  # fmt: skip
    detect(repo, run)
    source = repo.get_source(SRC)
    assert source.baseline_status == BaselineStatus.NOT_BASELINED and source.baseline_at is None
    assert "Not baselined" in (check_of(repo, run).error_detail or "")
    assert not [e for e in repo.list_audit() if e.action == "BASELINE_COMPLETED"]


def test_a_partly_failed_first_run_is_not_baselined(repo) -> None:
    run = crawl(repo, SRC, TODAY, rss=page_rows(), html=None, html_status=CheckStatus.FAILED)
    detect(repo, run)
    assert repo.get_source(SRC).baseline_status == BaselineStatus.NOT_BASELINED


# --- later crawls --------------------------------------------------------------------------------


def baselined(repo):
    detect(repo, crawl(repo, SRC, TODAY, html=page_rows()))


def test_the_same_page_again_creates_nothing_new(repo) -> None:
    baselined(repo)
    before = len(mine(repo))
    run = crawl(repo, SRC, date(2026, 11, 25), html=page_rows())
    result = detect(repo, run, today=date(2026, 11, 25))
    assert result.events == [] and len(mine(repo)) == before
    stats = check_of(repo, run).stats
    assert stats["known_older"] == 2 and stats["unchanged"] == 2 and stats["new"] == 0
    assert len(repo.list_seen_fingerprints(SRC)) == 2  # no duplicates


def test_a_new_link_is_new_and_an_old_dated_unseen_link_is_backdated(repo) -> None:
    baselined(repo)
    rows = [
        *page_rows(),
        row("Brand new notification", "RBI/2026-27/70", date(2026, 11, 25)),
        row("Old one that was never on the page", "RBI/2024-25/9", date(2025, 2, 1)),
    ]
    run = crawl(repo, SRC, date(2026, 11, 25), html=rows)
    result = detect(repo, run, today=date(2026, 11, 25))
    by_ref = {e.item.ref_no: e for e in result.events}
    assert set(by_ref) == {"RBI/2026-27/70", "RBI/2024-25/9"}
    assert by_ref["RBI/2026-27/70"].change == ChangeType.NEW
    assert by_ref["RBI/2026-27/70"].item.origin == ItemOrigin.CRAWL
    old = by_ref["RBI/2024-25/9"]
    assert old.change == ChangeType.BACKDATED and old.is_new
    assert "old date, new on page" in old.item.flags
    stats = check_of(repo, run).stats
    assert stats["new"] == 1 and stats["backdated"] == 1 and stats["known_older"] == 2


def test_a_changed_title_and_an_added_attachment_are_modified(repo) -> None:
    rows = [row("Notification on KYC", "RBI/2026-27/60", date(2026, 11, 20), attachments=["a.pdf"])]
    detect(repo, crawl(repo, SRC, TODAY, html=rows))

    retitled = [row("Notification on KYC (amended)", "RBI/2026-27/60", date(2026, 11, 20),
                    attachments=["a.pdf"])]  # fmt: skip
    day2 = date(2026, 11, 25)
    (event,) = detect(repo, crawl(repo, SRC, day2, html=retitled), today=day2).events
    assert event.change == ChangeType.MODIFIED and "title changed" in event.item.summary

    more = [row("Notification on KYC (amended)", "RBI/2026-27/60", date(2026, 11, 20),
                attachments=["a.pdf", "b.pdf"])]  # fmt: skip
    day3 = date(2026, 11, 26)
    (event,) = detect(repo, crawl(repo, SRC, day3, html=more), today=day3).events
    assert event.change == ChangeType.MODIFIED and "attachment added" in event.item.summary
    assert check_of(repo, repo.get_run(event.item.first_seen_run_id)) is not None


def test_a_changed_date_is_modified_not_new(repo) -> None:
    detect(repo, crawl(repo, SRC, TODAY, html=[row("KYC", "RBI/2026-27/60", date(2026, 11, 20))]))
    day2 = date(2026, 11, 25)
    rows = [row("KYC", "RBI/2026-27/60", date(2026, 11, 21))]
    (event,) = detect(repo, crawl(repo, SRC, day2, html=rows), today=day2).events
    assert event.change == ChangeType.MODIFIED and "date changed" in event.item.summary
    assert not event.is_new


# --- re-baseline ---------------------------------------------------------------------------------


def user(role: UserRole, name: str = "Ann") -> AppUser:
    return AppUser(id=f"u-{name}", name=name, email=f"{name}@example.invalid", role=role)


def test_rebaseline_deletes_nothing_and_creates_no_duplicates(repo) -> None:
    baselined(repo)
    items_before = {i.id for i in mine(repo)}
    seen_before = {s.id for s in repo.list_seen_fingerprints(SRC)}
    config = ConfigService(repo)

    with pytest.raises(ForbiddenError):
        config.rebaseline(user(UserRole.ADMIN), SRC)  # approver only
    config.rebaseline(user(UserRole.APPROVER), SRC)
    assert repo.get_source(SRC).baseline_status == BaselineStatus.NOT_BASELINED
    event = next(e for e in repo.list_audit() if e.action == "REBASELINE_REQUESTED")
    assert event.actor == "Ann" and event.entity_id == SRC
    with pytest.raises(ConflictError):
        config.rebaseline(user(UserRole.APPROVER), SRC)  # already waiting for its first crawl

    day2 = date(2026, 11, 25)
    run = crawl(repo, SRC, day2, html=page_rows())
    detect(repo, run, today=day2)  # window applied again, known fingerprints skipped
    assert {i.id for i in mine(repo)} == items_before
    assert {s.id for s in repo.list_seen_fingerprints(SRC)} == seen_before
    assert repo.get_source(SRC).baseline_status == BaselineStatus.BASELINED


# --- the review queue ----------------------------------------------------------------------------


def titles(response) -> set[str]:
    return {i.title for i in response.items}


def test_the_review_queue_leaves_out_baseline_items_by_default(repo) -> None:
    run = crawl(repo, SRC, TODAY, html=page_rows())
    detect(repo, run)
    hidden = ReviewService(repo, FirstRunConfig(baseline_items_in_review=False))
    shown = ReviewService(repo, FirstRunConfig(baseline_items_in_review=True))

    queue = hidden.list_items(None, None, None, None, None)
    assert not any(THIS_MONTH_A[1] in t for t in titles(queue))
    assert queue.counts.total == len(queue.items)

    full = shown.list_items(None, None, None, None, None)
    assert any(THIS_MONTH_A[1] in t for t in titles(full))
    assert all(i.origin == ItemOrigin.BASELINE for i in full.items if SRC == i.source_id)

    only = hidden.list_items(None, None, None, None, None, origin=ItemOrigin.BASELINE)
    assert titles(only) and all(i.origin == ItemOrigin.BASELINE for i in only.items)
    # item history (search across everything) still finds them
    assert any(
        i.ref_no == THIS_MONTH_A[1] for i in repo.list_items(ItemFilter(q="KYC")) + mine(repo)
    )
