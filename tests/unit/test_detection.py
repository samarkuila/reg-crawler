"""Detection on run sequences: every change type, a back-dated insert, an RSS-only item, zero items,
sanity warnings. Runs on the in-memory and the SQL repository."""

from datetime import date

import pytest

from app.schemas.api import ItemFilter
from app.schemas.entities import Document
from app.schemas.enums import (
    ChangeType,
    Channel,
    CheckChannel,
    CheckStatus,
    FetchStatus,
    ItemState,
    Priority,
)
from app.services.detection import DetectionService, content_hash, fingerprint
from tests.conftest import BACKENDS, make_backend
from tests.detection_helpers import HTML, RSS, XML, crawl, crawl_xml, make_source, row

D1, D2, D3 = date(2026, 11, 20), date(2026, 11, 23), date(2026, 11, 24)
SRC = "det-both"
KYC = "RBI/2026-27/45"


@pytest.fixture(params=BACKENDS)
def repo(request, tmp_path, sql_template):
    repository, _url, _files = make_backend(request.param, tmp_path, sql_template)
    make_source(repository, SRC)
    return repository


def detect(repo, run):
    return DetectionService(repo).process(run.id)


def item_of(repo, ref_no=None, title=None):
    return next(
        i
        for i in repo.list_items(ItemFilter())
        if i.source_id == SRC
        and (ref_no is None or i.ref_no == ref_no)
        and (title is None or i.title == title)
    )


# --- fingerprint -------------------------------------------------------------------------------


def test_fingerprint_uses_the_reference_number_when_there_is_one() -> None:
    a = fingerprint("s", "RBI/2026-27/45", "https://x/1")
    assert a == fingerprint("s", " rbi/2026-27/45 ", "https://x/other-address")
    assert a == fingerprint("s", "RBI/2026-27/ 45", "https://x/1")  # spacing does not matter
    assert a != fingerprint("s", "RBI/2026-27/46", "https://x/1")
    assert a != fingerprint("other", "RBI/2026-27/45", "https://x/1")


def test_fingerprint_falls_back_to_the_canonical_url() -> None:
    assert fingerprint("s", None, "https://x/1") == fingerprint("s", "  ", "https://x/1")
    assert fingerprint("s", None, "https://x/1") != fingerprint("s", None, "https://x/2")


# --- NEW ---------------------------------------------------------------------------------------


def test_first_run_makes_new_items_with_one_observation_per_channel(repo) -> None:
    run = crawl(
        repo, SRC, D1, rss=[row("KYC Directions", KYC, D1)], html=[row("KYC Directions", KYC, D1)]
    )
    result = detect(repo, run)
    (event,) = result.events
    assert event.change == ChangeType.NEW and event.is_new
    item = repo.get_item(event.item.id)
    assert item.internal_ref.startswith("TVSC-REG-") and item.state == ItemState.DETECTED
    assert item.channel == Channel.BOTH and item.channel_mismatch is False
    assert item.first_seen_run_id == run.id
    obs = repo.list_observations(item.id)
    assert {o.channel for o in obs} == {RSS, HTML} and {o.change_type for o in obs} == {
        ChangeType.NEW
    }
    assert all(o.prior_hash is None and len(o.current_hash) == 64 for o in obs)
    assert any(
        e.action == "regulatory_item.detected" and e.actor == "detection" for e in repo.list_audit()
    )


def test_internal_references_are_numbered_in_order(repo) -> None:
    run = crawl(repo, SRC, D1, html=[row("One", "A/1", D1), row("Two", "A/2", D1)])
    detect(repo, run)
    refs = sorted(i.internal_ref for i in repo.list_items(ItemFilter()) if i.source_id == SRC)
    assert refs[1] == refs[0][:-6] + f"{int(refs[0][-6:]) + 1:06d}"


def test_a_rerun_of_the_same_rows_changes_nothing(repo) -> None:
    detect(repo, crawl(repo, SRC, D1, html=[row("KYC Directions", KYC, D1)]))
    item = item_of(repo, KYC)
    run = crawl(repo, SRC, D2, html=[row("KYC Directions", KYC, D1)])
    audit_before = len(repo.list_audit())
    result = detect(repo, run)
    assert result.events == [] and result.unchanged == 1
    assert len(repo.list_observations(item.id)) == 1  # no new observation for "still there"
    assert repo.get_item(item.id).last_seen_at > item.last_seen_at
    assert len(repo.list_audit()) == audit_before + 1  # one batch event for the last-seen times


def test_the_displayed_date_never_decides_what_is_new(repo) -> None:
    # Same item, the website now shows a different date: still the same item, not a new one.
    detect(repo, crawl(repo, SRC, D1, html=[row("KYC Directions", KYC, D1)]))
    result = detect(repo, crawl(repo, SRC, D2, html=[row("KYC Directions", KYC, date(2026, 1, 1))]))
    assert result.events == []


def test_the_first_ever_run_has_no_backdating(repo) -> None:
    result = detect(repo, crawl(repo, SRC, D2, html=[row("Old notice", "A/9", date(2020, 1, 1))]))
    assert [e.change for e in result.events] == [ChangeType.NEW]


# --- BACKDATED ---------------------------------------------------------------------------------


def test_an_unseen_item_dated_before_the_last_healthy_run_is_backdated(repo) -> None:
    detect(repo, crawl(repo, SRC, D1, html=[row("First", "A/1", D1)]))
    result = detect(
        repo,
        crawl(
            repo, SRC, D2,
            html=[
                row("First", "A/1", D1),
                row("Inserted late", "A/2", date(2026, 11, 10)),  # before the last run's date
                row("Fresh", "A/3", D2),
            ],
        ),
    )  # fmt: skip
    changes = {e.item.ref_no: e.change for e in result.events}
    assert changes == {"A/2": ChangeType.BACKDATED, "A/3": ChangeType.NEW}
    assert {o.change_type for o in repo.list_observations(item_of(repo, "A/2").id)} == {
        ChangeType.BACKDATED
    }


def test_a_date_equal_to_the_last_run_date_is_not_backdated(repo) -> None:
    detect(repo, crawl(repo, SRC, D1, html=[row("First", "A/1", D1)]))
    result = detect(repo, crawl(repo, SRC, D2, html=[row("Same day", "A/2", D1)]))
    assert result.events[0].change == ChangeType.NEW


# --- MODIFIED ----------------------------------------------------------------------------------


def test_a_changed_title_is_modified_and_keeps_the_prior_hash(repo) -> None:
    detect(repo, crawl(repo, SRC, D1, html=[row("KYC Directions", KYC, D1)]))
    first = item_of(repo, KYC)
    old_hash = repo.list_observations(first.id)[0].current_hash
    result = detect(repo, crawl(repo, SRC, D2, html=[row("KYC Directions (Amended)", KYC, D1)]))
    (event,) = result.events
    assert event.change == ChangeType.MODIFIED and not event.is_new
    item = repo.get_item(first.id)
    assert item.title == "KYC Directions (Amended)" and item.internal_ref == first.internal_ref
    last = repo.list_observations(first.id)[-1]
    assert last.change_type == ChangeType.MODIFIED and last.prior_hash == old_hash
    assert last.current_hash != old_hash


def test_changed_feed_text_is_modified_but_html_is_compared_with_html(repo) -> None:
    # RSS carries a description, HTML does not: their hashes differ, but that is not a change.
    rss = row("KYC Directions", KYC, D1, description_html="<p>Version one</p>")
    detect(repo, crawl(repo, SRC, D1, rss=[rss], html=[row("KYC Directions", KYC, D1)]))
    same = detect(repo, crawl(repo, SRC, D2, rss=[rss], html=[row("KYC Directions", KYC, D1)]))
    assert same.events == []
    changed = row("KYC Directions", KYC, D1, description_html="<p>Version two</p>")
    result = detect(
        repo, crawl(repo, SRC, D3, rss=[changed], html=[row("KYC Directions", KYC, D1)])
    )
    (event,) = result.events
    assert event.change == ChangeType.MODIFIED
    obs = [o for o in repo.list_observations(event.item.id) if o.run_id == result.run_id]
    assert {o.channel for o in obs} == {RSS, HTML}  # one observation per channel that saw it


def test_a_modified_item_that_was_already_decided_goes_back_for_review(repo) -> None:
    detect(repo, crawl(repo, SRC, D1, html=[row("KYC Directions", KYC, D1)]))
    item = item_of(repo, KYC)
    repo.add_document(
        Document(id="doc-x", item_id=item.id, fetch_status=FetchStatus.MANUAL),
        None,
        ItemState.DECIDED,
        "t",
    )
    assert repo.get_item(item.id).state == ItemState.DECIDED
    detect(repo, crawl(repo, SRC, D2, html=[row("KYC Directions - updated", KYC, D1)]))
    assert repo.get_item(item.id).state == ItemState.DETECTED


# --- REAPPEARED --------------------------------------------------------------------------------


def test_an_item_missing_for_a_healthy_run_and_back_is_reappeared(repo) -> None:
    detect(repo, crawl(repo, SRC, D1, html=[row("A", "A/1", D1), row("B", "B/1", D1)]))
    detect(repo, crawl(repo, SRC, D2, html=[row("B", "B/1", D1)]))  # A is absent, site answered
    result = detect(repo, crawl(repo, SRC, D3, html=[row("A", "A/1", D1), row("B", "B/1", D1)]))
    assert {e.item.ref_no: e.change for e in result.events} == {"A/1": ChangeType.REAPPEARED}


def test_a_blocked_run_is_not_an_absence(repo) -> None:
    detect(repo, crawl(repo, SRC, D1, html=[row("A", "A/1", D1)]))
    detect(repo, crawl(repo, SRC, D2, html_status=CheckStatus.BLOCKED))  # no rows: we were blocked
    result = detect(repo, crawl(repo, SRC, D3, html=[row("A", "A/1", D1)]))
    assert result.events == []  # A was never seen to leave


# --- RSS vs HTML -------------------------------------------------------------------------------


def test_an_item_only_in_the_feed_is_flagged_as_a_channel_mismatch(repo) -> None:
    result = detect(
        repo,
        crawl(repo, SRC, D1, rss=[row("Feed only", "A/1", D1), row("Both", "A/2", D1)],
              html=[row("Both", "A/2", D1)]),
    )  # fmt: skip
    by_ref = {e.item.ref_no: e.item for e in result.events}
    assert (by_ref["A/1"].channel, by_ref["A/1"].channel_mismatch) == (Channel.RSS, True)
    assert (by_ref["A/2"].channel, by_ref["A/2"].channel_mismatch) == (Channel.BOTH, False)
    assert {o.channel for o in repo.list_observations(by_ref["A/1"].id)} == {RSS}


def test_an_item_only_on_the_page_is_flagged_too(repo) -> None:
    result = detect(
        repo,
        crawl(
            repo,
            SRC,
            D1,
            rss=[row("x", "A/0", D1)],
            html=[row("Page only", "A/1", D1), row("x", "A/0", D1)],
        ),
    )
    item = next(e.item for e in result.events if e.item.ref_no == "A/1")
    assert (item.channel, item.channel_mismatch) == (Channel.HTML, True)


def test_no_mismatch_is_claimed_when_one_channel_did_not_answer(repo) -> None:
    result = detect(
        repo,
        crawl(repo, SRC, D1, rss=[row("Feed only", "A/1", D1)], html_status=CheckStatus.FAILED),
    )
    item = result.events[0].item
    assert item.channel == Channel.RSS and item.channel_mismatch is False


def test_a_mismatch_clears_when_the_other_channel_catches_up(repo) -> None:
    detect(repo, crawl(repo, SRC, D1, rss=[row("Late", "A/1", D1)], html=[row("x", "A/0", D1)]))
    item = item_of(repo, "A/1")
    assert item.channel_mismatch is True
    detect(repo, crawl(repo, SRC, D2, rss=[row("Late", "A/1", D1)], html=[row("Late", "A/1", D1)]))
    item = repo.get_item(item.id)
    assert (item.channel, item.channel_mismatch) == (Channel.BOTH, False)


def test_a_source_with_one_channel_never_has_a_mismatch(repo) -> None:
    result = detect(repo, crawl(repo, SRC, D1, html=[row("Only html", "A/1", D1)]))
    assert result.events[0].item.channel_mismatch is False


# --- sanity checks -----------------------------------------------------------------------------


def test_zero_items_gives_a_warning_and_a_note_on_the_check(repo) -> None:
    run = crawl(repo, SRC, D1, html=[])
    result = detect(repo, run)
    (warning,) = result.warnings
    assert (warning.kind, warning.channel) == ("ZERO_ITEMS", HTML)
    check = next(c for c in repo.list_source_checks(run.id) if c.channel == HTML)
    assert check.status == CheckStatus.ZERO_ITEMS and "Warning" in check.error_detail


def test_zero_items_mentions_the_usual_count_when_there_is_history(repo) -> None:
    for n in range(3):
        detect(
            repo,
            crawl(
                repo,
                SRC,
                date(2026, 11, 10 + n),
                html=[row(f"I{k}", f"R/{n}-{k}", D1) for k in range(8)],
            ),
        )
    result = detect(repo, crawl(repo, SRC, D2, html=[]))
    assert "usually returns about 8" in result.warnings[0].detail


def history(repo, counts: list[int]) -> None:
    for n, count in enumerate(counts):
        detect(
            repo,
            crawl(
                repo,
                SRC,
                date(2026, 11, 1 + n),
                html=[row(f"I{k}", f"R/{n}-{k}", D1) for k in range(count)],
            ),
        )


def test_a_drop_of_more_than_half_against_the_median_is_a_warning(repo) -> None:
    history(repo, [10, 10, 10, 10])
    run = crawl(repo, SRC, D2, html=[row(f"N{k}", f"Z/{k}", D2) for k in range(4)])
    result = detect(repo, run)
    (warning,) = result.warnings
    assert warning.kind == "DROP" and "4 items" in warning.detail and "10" in warning.detail
    check = next(c for c in repo.list_source_checks(run.id) if c.channel == HTML)
    assert check.status == CheckStatus.OK  # a warning, not a failure
    assert "Warning" in check.error_detail


def test_a_drop_of_exactly_half_is_not_a_warning(repo) -> None:
    history(repo, [10, 10, 10, 10])
    result = detect(repo, crawl(repo, SRC, D2, html=[row(f"N{k}", f"Z/{k}", D2) for k in range(5)]))
    assert result.warnings == []


def test_too_little_history_gives_no_drop_warning(repo) -> None:
    history(repo, [10, 10])
    result = detect(repo, crawl(repo, SRC, D2, html=[row("N", "Z/1", D2)]))
    assert result.warnings == []


def test_the_median_ignores_one_odd_run(repo) -> None:
    history(repo, [10, 2, 10, 10, 10])
    result = detect(repo, crawl(repo, SRC, D2, html=[row(f"N{k}", f"Z/{k}", D2) for k in range(9)]))
    assert result.warnings == []


def test_a_warning_is_noted_once(repo) -> None:
    run = crawl(repo, SRC, D1, html=[])
    detect(repo, run)
    detect(repo, run)  # processing a run twice must not pile up the same note
    check = next(c for c in repo.list_source_checks(run.id) if c.channel == HTML)
    assert check.error_detail.count("Warning") == 1


def test_a_data_file_source_is_not_judged_on_item_counts(repo) -> None:
    make_source(repo, "det-xml", rss_url=None)
    for n in range(4):
        crawl_xml(
            repo,
            "det-xml",
            date(2026, 11, 1 + n),
            [row(f"C{n}{k}", f"Q/{n}{k}", D1) for k in range(10)],
        )
    result = detect(repo, crawl_xml(repo, "det-xml", D2, []))
    assert result.warnings == []


# --- priority, summary, relevance --------------------------------------------------------------


def test_urgent_priority_and_summary_come_from_the_row(repo) -> None:
    make_source(repo, "det-xml", rss_url=None)
    run = crawl_xml(
        repo, "det-xml", D1,
        [row("UNSC amended", "QDi.901", None, priority="URGENT", summary="alias added: Al Fa")],
    )  # fmt: skip
    (event,) = detect(repo, run).events
    assert event.item.priority == Priority.URGENT and event.item.summary == "alias added: Al Fa"
    assert [o.channel for o in repo.list_observations(event.item.id)] == [XML]


def test_relevance_uses_global_and_source_keywords_and_never_drops_an_item(repo) -> None:
    make_source(repo, "det-kw", include_keywords=["digital lending"], exclude_keywords=["webinar"])
    run = crawl(
        repo, "det-kw", D1,
        html=[
            row("KYC norms for NBFCs", "K/1", D1),
            row("Digital lending: NBFC guidelines", "K/2", D1),
            row("Auction of securities", "K/3", D1),
            row("Webinar on KYC", "K/4", D1),
            row("A circular", "K/5", D1),
        ],
    )  # fmt: skip
    result = detect(repo, run)
    scores = {e.item.ref_no: e.item for e in result.events}
    assert len(scores) == 5  # nothing was hidden
    assert (
        scores["K/2"].relevance_score
        > scores["K/1"].relevance_score
        > scores["K/5"].relevance_score
    )
    assert (
        scores["K/3"].relevance_score < scores["K/5"].relevance_score
    )  # global exclude: 'auction'
    assert (
        scores["K/4"].relevance_score < scores["K/1"].relevance_score
    )  # source exclude: 'webinar'
    assert "'NBFCs'" not in scores["K/1"].relevance_reason  # whole words only
    assert "'KYC'" in scores["K/1"].relevance_reason


def test_content_hash_ignores_spacing_and_case_of_the_reference_number() -> None:
    from app.schemas.entities import RawListingItem, now_ist

    def make(ref):
        return RawListingItem(
            id="r", run_id="x", source_id="s", channel=CheckChannel.HTML, title="T  x",
            url="https://x/1", ref_no=ref, fetched_at=now_ist(),
        )  # fmt: skip

    assert content_hash(make("rbi/1")) == content_hash(make("RBI / 1".replace(" ", "")))
