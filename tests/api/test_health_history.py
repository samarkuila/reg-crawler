"""Source health and Item history pages (FR-26, FR-27): shells, grid, run polling, chain, CSV."""

import csv
import hashlib
import io

from app.api.deps import get_services
from app.schemas.enums import Cycle
from tests.api.conftest import ACCEPT_IORS, DIVYA, EGAZETTE_CHECK, EOD, GEORGE, as_user

IORS = "item-iors-faqs"
API = "/api/v1"


# ---------- Source health ----------


def test_health_page_shell_and_script(client) -> None:
    html = client.get("/health").text
    for marker in (
        'id="sh-run"',
        'id="sh-rows"',
        'id="sh-head"',
        "/static/js/pages/health.js",
        "What the statuses mean",
        "Run one source",
        "Run all now",
    ):
        assert marker in html, marker
    assert "not built yet" not in html
    assert client.get("/static/js/pages/health.js").status_code == 200


def test_grid_covers_the_last_four_cycles_per_source_and_channel(client) -> None:
    grid = client.get(f"{API}/source-health/grid").json()
    assert grid["cycle_labels"] == ["19.11 Morning", "19.11 EOD", "20.11 Morning", "20.11 EOD"]
    rows = {(r["authority"], r["channel"]): r for r in grid["rows"]}
    assert [k for k in rows][:2] == [("RBI", "RSS"), ("RBI", "HTML")]
    labels = lambda key: [c["label"] for c in rows[key]["cells"]]  # noqa: E731
    assert labels(("RBI", "RSS")) == ["OK"] * 4
    assert labels(("RBI", "HTML"))[-1] == "1 mismatch"
    assert labels(("TRAI", "HTML"))[2] == "Zero items"
    assert labels(("IRDAI", "HTML"))[1] == "Failed (timeout)"
    assert labels(("eGazette", "HTML"))[-1] == "Blocked"
    assert rows[("UNSC", "XML")]["items_latest"] == "1 change"
    assert rows[("eGazette", "HTML")]["items_latest"] == "—"


def test_row_actions_follow_the_latest_status(client) -> None:
    rows = {
        (r["authority"], r["channel"]): r
        for r in client.get(f"{API}/source-health/grid").json()["rows"]
    }
    kinds = lambda key: [a["kind"] for a in rows[key]["actions"]]  # noqa: E731
    assert kinds(("RBI", "RSS")) == ["snapshot"]
    assert kinds(("RBI", "HTML")) == ["compare"]
    assert kinds(("eGazette", "HTML")) == ["manual", "log"]
    assert rows[("eGazette", "HTML")]["needs_attention"] is True


def test_marking_a_blocked_source_checked_updates_the_grid(client) -> None:
    as_user(client, DIVYA).post(
        f"{API}/source-checks/{EGAZETTE_CHECK}/manual-check", json={"note": "Checked in browser"}
    )
    row = next(
        r
        for r in client.get(f"{API}/source-health/grid").json()["rows"]
        if r["authority"] == "eGazette"
    )
    assert row["cells"][-1]["label"] == "Blocked · checked manually"
    assert row["needs_attention"] is False and [a["kind"] for a in row["actions"]] == ["log"]


def test_compare_rss_vs_html_lists_one_channel_items(client) -> None:
    out = client.get(
        f"{API}/source-health/compare", params={"source_id": "rbi", "cycle": EOD}
    ).json()
    assert out["rss_only"] + out["html_only"] >= 1 and out["both"] >= 1
    assert out["items"][0]["seen_in"] != "Both"  # disagreements come first
    assert (
        client.get(f"{API}/source-health/compare", params={"source_id": "nope"}).status_code == 404
    )


def test_check_log_explains_a_blocked_check(client) -> None:
    out = client.get(f"{API}/source-health/checks/{EGAZETTE_CHECK}/log").json()
    text = "\n".join(out["lines"])
    assert "Blocked" in text and "never bypasses" in text and "Not yet checked manually" in text
    assert client.get(f"{API}/source-health/checks/nope/log").status_code == 404


def test_stored_snapshot_is_served_as_text_after_its_hash_is_checked(client) -> None:
    grid = client.get(f"{API}/source-health/grid").json()
    snap_id = next(
        a["snapshot_id"] for r in grid["rows"] for a in r["actions"] if a["kind"] == "snapshot"
    )
    r = client.get(f"{API}/snapshots/{snap_id}/content")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/plain")
    assert r.headers["x-content-type-options"] == "nosniff"
    repo = get_services().repo
    assert hashlib.sha256(r.content).hexdigest() == repo.get_snapshot(snap_id).sha256
    assert client.get(f"{API}/snapshots/nope/content").status_code == 404


def test_tampered_stored_copy_is_refused(client) -> None:
    repo = get_services().repo
    snap = repo.get_snapshot("snap-2026-11-20-eod-rbi-rss")
    repo._blobs[snap.sha256] = b"changed"  # noqa: SLF001 - simulate on-disk tampering
    r = client.get(f"{API}/snapshots/{snap.id}/content")
    assert r.status_code == 409 and "SHA-256" in r.json()["detail"]


def test_running_run_reports_progress_for_polling(client) -> None:
    repo = get_services().repo
    run = repo.create_run(
        Cycle.ADHOC, __import__("datetime").date(2026, 11, 21), "Divya G.", "Divya G."
    )
    grid = client.get(f"{API}/source-health/grid").json()
    assert grid["current_run"]["id"] == run.id and grid["current_run"]["status"] == "RUNNING"
    detail = client.get(f"{API}/runs/{run.id}").json()
    assert detail["run"]["status"] == "RUNNING"
    assert detail["sources_done"] == 0 and detail["sources_total"] >= 5


def test_polling_interval_is_three_seconds() -> None:
    from pathlib import Path

    js = (Path(__file__).resolve().parents[2] / "app/static/js/pages/health.js").read_text("utf-8")
    assert "POLL_MS = 3000" in js and "/runs/" in js


# ---------- Item history ----------


def test_history_page_shell_and_script(client) -> None:
    html = client.get("/history").text
    for marker in (
        'id="hi-form"',
        'id="h-q"',
        'id="h-a"',
        'id="h-f"',
        'id="h-t"',
        'id="hi-list"',
        'id="hi-trail"',
        "Export trail (CSV)",
        "/static/js/pages/history.js",
    ):
        assert marker in html, marker
    assert "not built yet" not in html
    assert client.get("/static/js/pages/history.js").status_code == 200


def test_search_by_title_authority_and_dates(client) -> None:
    hits = client.get(f"{API}/history/search", params={"q": "IoRS"}).json()
    assert hits["total"] == 1 and hits["hits"][0]["internal_ref"] == "TVSC-REG-2026-000214"
    assert hits["hits"][0]["note"] == "Modified 20.11.2026" and "RBI" in hits["authorities"]
    by_ref = client.get(f"{API}/history/search", params={"q": "TVSC-REG-2026-000214"}).json()
    assert by_ref["total"] == 1
    assert client.get(f"{API}/history/search", params={"authority": "IRDAI"}).json()["total"] >= 1
    window = {"from": "2026-11-01", "to": "2026-11-21"}
    assert client.get(f"{API}/history/search", params={"q": "IoRS", **window}).json()["total"] == 1
    far = {"from": "2025-01-01", "to": "2025-01-31"}
    assert client.get(f"{API}/history/search", params={"q": "IoRS", **far}).json()["total"] == 0
    assert (
        client.get(
            f"{API}/history/search", params={"from": "2026-12-01", "to": "2026-11-01"}
        ).status_code
        == 422
    )


def test_search_never_hides_dismissed_items(client) -> None:
    as_user(client, DIVYA).post(
        f"{API}/items/bulk-dismiss", json={"item_ids": ["item-rbi-mmo"], "reason": "Not relevant"}
    )
    assert any(
        h["id"] == "item-rbi-mmo" for h in client.get(f"{API}/history/search").json()["hits"]
    )


def accept_and_draft(client) -> None:
    as_user(client, DIVYA).post(f"{API}/items/{IORS}/decision", json=ACCEPT_IORS)
    client.get(f"{API}/daily-update/preview", params={"cycle": EOD})  # builds the draft


def test_iors_chain_has_six_steps_with_working_links(client) -> None:
    accept_and_draft(client)
    trail = client.get(f"{API}/history/{IORS}/trail").json()
    steps = trail["steps"]
    assert [s["number"] for s in steps] == [1, 2, 3, 4, 5, 6]
    assert [s["kind"] for s in steps] == [
        "email",
        "decision",
        "document",
        "change",
        "snapshot",
        "source_config",
    ]
    assert all(s["present"] for s in steps)
    assert steps[0]["badge"]["label"] == "Draft" and "row" in steps[0]["text"]
    assert "Divya G." in steps[1]["text"] and steps[1]["note"].startswith("No pre-filled")
    assert steps[2]["hashes"][0]["sha256"] and steps[3]["badge"]["label"] == "Modified"
    assert [h["label"] for h in steps[3]["hashes"]] == ["Before", "After"]
    assert steps[3]["hashes"][1]["sha256"] == steps[2]["hashes"][0]["sha256"]
    assert len(steps[4]["hashes"][0]["sha256"]) == 64 and "version" in steps[5]["text"]
    for step in steps:
        for link in step["links"]:
            assert client.get(link["href"]).status_code == 200, link["href"]
    assert {link["label"] for s in steps for link in s["links"]} == {
        "Open email draft",
        "Open stored copy",
        "Open snapshot",
        "View source settings",
    }


def test_chain_lists_all_six_steps_even_before_review(client) -> None:
    steps = client.get(f"{API}/history/{IORS}/trail").json()["steps"]
    assert len(steps) == 6 and not steps[0]["present"] and not steps[1]["present"]
    assert all(s["present"] for s in steps[2:])
    assert client.get(f"{API}/history/nope/trail").status_code == 404


def test_edited_fields_are_named_in_the_decision_step(client) -> None:
    as_user(client, DIVYA).post(
        f"{API}/items/{IORS}/decision", json={**ACCEPT_IORS, "authority": "RBI (edited)"}
    )
    note = client.get(f"{API}/history/{IORS}/trail").json()["steps"][1]["note"]
    assert note == "Fields edited by reviewer: Authority"


def test_approved_email_shows_the_approver(client) -> None:
    accept_and_draft(client)
    update_id = client.get(f"{API}/daily-update/preview", params={"cycle": EOD}).json()["id"]
    as_user(client, DIVYA).post(
        f"{API}/source-checks/{EGAZETTE_CHECK}/manual-check", json={"note": "ok"}
    )
    assert (
        as_user(client, GEORGE).post(f"{API}/daily-update/{update_id}/approve").status_code == 200
    )
    step = client.get(f"{API}/history/{IORS}/trail").json()["steps"][0]
    assert step["badge"]["label"] == "Approved" and "approved by George K." in step["text"]


def test_trail_exports_to_csv(client) -> None:
    accept_and_draft(client)
    r = client.get(f"{API}/history/{IORS}/trail.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "trail-tvsc-reg-2026-000214.csv" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.content.decode("utf-8-sig"))))
    assert rows[0][:3] == ["Step", "Stage", "What happened"] and len(rows) == 7
    assert [row[1] for row in rows[1:]] == [
        "email",
        "decision",
        "document",
        "change",
        "snapshot",
        "source_config",
    ]
    assert any(len(row[6]) >= 64 for row in rows[1:])  # SHA-256 values are in the export
    assert client.get(f"{API}/history/nope/trail.csv").status_code == 404
