"""Email preview page: shell, FR-33 date wording, FR-32 cycle export, SME CC, no-send guarantee."""

import csv
import io
from email import message_from_bytes, policy

from app.services.cycle_export import HEADERS, _safe
from tests.api.conftest import ACCEPT_IORS, AUDIT, DIVYA, EGAZETTE_CHECK, EOD, GEORGE, as_user

IORS = "item-iors-faqs"
PIB = "item-pib-2336151"
LOW = ["item-rbi-mmo", "item-rbi-auction", "item-trai-subs"]


def preview(client):
    return client.get("/api/v1/daily-update/preview", params={"cycle": EOD}).json()


def test_email_page_renders_shell_and_script(client) -> None:
    html = client.get("/email").text
    for marker in (
        'id="em-checks"',
        'id="em-rows"',
        'id="em-frame"',
        'id="e-sub"',
        'id="e-to"',
        'id="e-cc"',
        'id="em-copy"',
        'id="em-eml"',
        'id="em-approve"',
        "/static/js/pages/email.js",
        "Export cycle to Excel",
        'sandbox="allow-same-origin"',
    ):
        assert marker in html, marker
    assert "not built yet" not in html
    assert client.get("/static/js/pages/email.js").status_code == 200


def test_different_issue_and_publish_dates_are_both_shown(client) -> None:
    body = {**ACCEPT_IORS, "issue_date": "2026-11-19", "publish_date": "2026-11-20"}
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=body)
    data = preview(client)
    row = next(r for r in data["rows"] if r["item_id"] == IORS)
    assert row["date_note"] == "Date: 19.11.2026 · Published on website: 20.11.2026"
    assert "Date: 19.11.2026 · Published on website: 20.11.2026" in data["html"]
    pib = next(r for r in data["rows"] if r["item_id"] == PIB)
    assert pib["date_note"] is None  # same day (or unknown): nothing extra to say


def test_same_issue_and_publish_date_adds_no_note(client) -> None:
    body = {**ACCEPT_IORS, "issue_date": "2026-11-19", "publish_date": "2026-11-19"}
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=body)
    assert "Published on website" not in preview(client)["html"]


def test_other_function_item_puts_the_sme_head_in_cc_of_the_eml(client) -> None:
    body = {**ACCEPT_IORS, "treatment": "OTHER_FUNCTION", "sme_function": "treasury"}
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=body)
    update_id = preview(client)["id"]
    as_user(client, DIVYA).post(
        f"/api/v1/source-checks/{EGAZETTE_CHECK}/manual-check", json={"note": "Checked"}
    )
    approved = as_user(client, GEORGE).post(f"/api/v1/daily-update/{update_id}/approve")
    assert approved.status_code == 200
    r = client.get(f"/api/v1/daily-update/{update_id}/export.eml")
    msg = message_from_bytes(r.content, policy=policy.default)
    assert "Treasury head" in msg["Cc"] and msg["X-Unsent"] == "1"


def test_cycle_export_lists_every_captured_item_with_a_blank_marking_column(client) -> None:
    as_user(client, DIVYA)
    client.post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    client.post("/api/v1/items/bulk-dismiss", json={"item_ids": LOW, "reason": "Not relevant"})
    r = client.get("/api/v1/daily-update/cycle-export.csv", params={"cycle": EOD})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "captured-items-2026-11-20-eod.csv" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")  # BOM so Excel reads the characters correctly
    rows = list(csv.DictReader(io.StringIO(r.content.decode("utf-8-sig"))))
    items = client.get("/api/v1/items", params={"cycle": EOD}).json()["items"]
    assert len(rows) == len(items) and list(rows[0]) == HEADERS
    assert all(row["Compliance marking"] == "" for row in rows)
    by_ref = {row["Internal ref"]: row for row in rows}
    outcomes = {by_ref[i["internal_ref"]]["Review outcome"] for i in items if i["id"] in LOW}
    assert outcomes == {"Dismissed"}  # dismissed items are still in the export
    iors = by_ref[next(i["internal_ref"] for i in items if i["id"] == IORS)]
    assert iors["Review outcome"] == "Accepted" and iors["In daily update"] == "Yes"
    assert any(row["Relevance"] == "Low" for row in rows)  # low relevance is not dropped


def test_cycle_export_roles_and_validation(client) -> None:
    url = "/api/v1/daily-update/cycle-export.csv"
    assert as_user(client, AUDIT).get(url, params={"cycle": EOD}).status_code == 403
    assert as_user(client, DIVYA).get(url, params={"cycle": "nope"}).status_code == 422
    assert as_user(client, DIVYA).get(url).status_code == 200  # defaults to the latest cycle


def test_export_neutralises_spreadsheet_formulas() -> None:
    assert _safe('=HYPERLINK("x")') == '\'=HYPERLINK("x")'
    assert _safe("Circular 12") == "Circular 12"


def test_reordering_changes_the_email_and_stays_a_draft(client) -> None:
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    data = preview(client)
    r = client.post(f"/api/v1/daily-update/{data['id']}/reorder", json={"item_ids": [IORS, PIB]})
    html = r.json()["html"]
    assert html.index("Inter-operable") < html.index("Consent Manager")
    assert r.json()["status"] == "DRAFT"
