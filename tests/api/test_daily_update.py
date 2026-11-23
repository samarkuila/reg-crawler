from email import message_from_bytes, policy

from tests.api.conftest import (
    ACCEPT_IORS,
    AUDIT,
    DIVYA,
    EGAZETTE_CHECK,
    EOD,
    GEORGE,
    SAMARENDRA,
    as_user,
)

IORS = "item-iors-faqs"
PIB = "item-pib-2336151"


def preview(client, cycle=EOD):
    return client.get("/api/v1/daily-update/preview", params={"cycle": cycle})


def acknowledge(client, user=DIVYA):
    r = as_user(client, user).post(
        f"/api/v1/source-checks/{EGAZETTE_CHECK}/manual-check", json={"note": "Checked"}
    )
    assert r.status_code == 201


def test_preview_is_built_from_the_reviewed_pib_item(client) -> None:
    body = preview(client).json()
    assert body["status"] == "DRAFT" and body["no_update"] is False
    assert body["subject"] == "Daily Regulatory Update – 20.11.2026 (EOD)"
    assert len(body["rows"]) == 1
    row = body["rows"][0]
    assert row["serial_no"] == 1 and row["item_id"] == PIB
    assert row["ref_no_and_date"] == "PIB Release ID 2336151 dated 14.11.2026"
    assert row["authority"] == "MeitY" and row["applicability"] == "Yes"
    assert row["effective_date"] == "13.11.2026" and row["status_hex"] == "#F2B200"
    assert row["status_label"] == "Action yet to start"
    assert "Dear All," in body["html"] and "Regards," in body["html"]
    assert "Consent Manager Registration Framework" in body["html"]
    assert body["to"] == ["TVSCS Compliance team", "Home Credit Compliance team"]
    assert body["cc"] == []  # the IRDAI item is not included, so no SME head is copied


def test_preview_defaults_to_latest_cycle_and_validates_cycle(client) -> None:
    assert preview(client, cycle=None).json()["cycle"] == EOD
    assert preview(client, cycle="nope").status_code == 422


def test_items_without_a_decision_never_reach_the_email(client) -> None:
    html = preview(client).json()["html"]
    assert "UN sanctions list" not in html and "Inter-operable" not in html


def test_accepted_item_joins_the_draft_and_other_function_copies_sme_head(client) -> None:
    body = {**ACCEPT_IORS, "treatment": "OTHER_FUNCTION", "sme_function": "treasury"}
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=body)
    data = preview(client).json()
    assert [r["item_id"] for r in data["rows"]] == [PIB, IORS]
    assert [r["serial_no"] for r in data["rows"]] == [1, 2]
    assert data["cc"] == ["Treasury head"]


def test_preview_is_stable_and_only_logs_real_changes(client, audit_count) -> None:
    preview(client)
    n = audit_count()
    preview(client)
    preview(client)
    assert audit_count() == n


def test_pre_send_checks_name_the_unchecked_source(client) -> None:
    checks = preview(client).json()["checks"]
    sources = checks[1]
    assert sources["ok"] is False and "eGazette (blocked)" in sources["text"]
    acknowledge(client)
    checks = preview(client).json()["checks"]
    assert checks[1]["ok"] is True and "eGazette checked manually by Divya G." in checks[1]["text"]


def test_manual_check_is_named_in_the_email(client) -> None:
    acknowledge(client)
    assert "eGazette checked manually by Divya G." in preview(client).json()["html"]


def test_unchecked_failed_source_blocks_approval(client) -> None:
    update_id = preview(client).json()["id"]
    r = as_user(client, GEORGE).post(f"/api/v1/daily-update/{update_id}/approve")
    assert r.status_code == 409 and "eGazette" in r.json()["detail"]
    assert preview(client).json()["status"] == "DRAFT"


def test_no_update_email_cannot_be_approved_while_a_source_failure_is_unchecked(client) -> None:
    as_user(client, DIVYA).post(
        f"/api/v1/items/{PIB}/decision", json={"action": "dismiss", "dismiss_reason": "Not needed"}
    )
    data = preview(client).json()
    assert data["no_update"] is True and data["rows"] == []
    assert "There were no relevant regulatory updates for 20.11.2026." in data["html"]
    r = as_user(client, GEORGE).post(f"/api/v1/daily-update/{data['id']}/approve")
    assert r.status_code == 409
    acknowledge(client)
    assert (
        as_user(client, GEORGE).post(f"/api/v1/daily-update/{data['id']}/approve").status_code
        == 200
    )


def test_only_an_approver_can_approve(client) -> None:
    update_id = preview(client).json()["id"]
    acknowledge(client)
    for user in (DIVYA, SAMARENDRA, AUDIT):
        r = as_user(client, user).post(f"/api/v1/daily-update/{update_id}/approve")
        assert r.status_code == 403, user
    assert client.post("/api/v1/daily-update/nope/approve").status_code == 403
    assert as_user(client, GEORGE).post("/api/v1/daily-update/nope/approve").status_code == 404


def test_approver_cannot_approve_what_they_prepared(client) -> None:
    as_user(client, GEORGE).post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    acknowledge(client, GEORGE)
    data = preview(client).json()
    assert data["can_approve"] is False
    assert any("different approver" in c["text"] for c in data["checks"])
    r = client.post(f"/api/v1/daily-update/{data['id']}/approve")
    assert r.status_code == 403 and "different approver" in r.json()["detail"]


def test_approval_records_approver_and_publishes_items(client, audit_count) -> None:
    data = preview(client).json()
    assert data["can_approve"] is False  # unchecked source
    acknowledge(client)
    as_user(client, GEORGE)
    assert preview(client).json()["can_approve"] is True
    before = audit_count()
    r = client.post(f"/api/v1/daily-update/{data['id']}/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "APPROVED" and body["approved_by"] == "George K."
    assert body["approved_at"] and body["can_approve"] is False
    assert audit_count() == before + 2  # the approval and the item moving to PUBLISHED
    assert client.get(f"/api/v1/items/{PIB}").json()["item"]["state"] == "PUBLISHED"
    assert client.post(f"/api/v1/daily-update/{data['id']}/approve").status_code == 409


def test_approved_update_is_frozen(client) -> None:
    data = preview(client).json()
    acknowledge(client)
    as_user(client, GEORGE).post(f"/api/v1/daily-update/{data['id']}/approve")
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    again = preview(client).json()
    assert again["status"] == "APPROVED" and [r["item_id"] for r in again["rows"]] == [PIB]
    r = client.post(f"/api/v1/daily-update/{data['id']}/reorder", json={"item_ids": [PIB]})
    assert r.status_code == 409


def test_reorder_sets_serial_numbers(client, audit_count) -> None:
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    update_id = preview(client).json()["id"]
    before = audit_count()
    r = client.post(f"/api/v1/daily-update/{update_id}/reorder", json={"item_ids": [IORS, PIB]})
    assert r.status_code == 200
    assert [(x["serial_no"], x["item_id"]) for x in r.json()["rows"]] == [(1, IORS), (2, PIB)]
    assert audit_count() == before + 1
    assert [x["item_id"] for x in preview(client).json()["rows"]] == [IORS, PIB]  # order sticks


def test_reorder_validation_and_roles(client) -> None:
    update_id = preview(client).json()["id"]
    url = f"/api/v1/daily-update/{update_id}/reorder"
    c = as_user(client, DIVYA)
    assert c.post(url, json={"item_ids": [IORS]}).status_code == 422  # not a row of this update
    assert c.post(url, json={"item_ids": [PIB, PIB]}).status_code == 422
    assert c.post(url, json={"item_ids": []}).status_code == 422
    assert as_user(client, AUDIT).post(url, json={"item_ids": [PIB]}).status_code == 403
    assert (
        as_user(client, DIVYA)
        .post("/api/v1/daily-update/nope/reorder", json={"item_ids": [PIB]})
        .status_code
        == 404
    )


def test_export_needs_approval_then_gives_an_unsent_eml(client) -> None:
    update_id = preview(client).json()["id"]
    for ext in ("eml", "html"):
        assert client.get(f"/api/v1/daily-update/{update_id}/export.{ext}").status_code == 409
    acknowledge(client)
    as_user(client, GEORGE).post(f"/api/v1/daily-update/{update_id}/approve")
    r = as_user(client, DIVYA).get(f"/api/v1/daily-update/{update_id}/export.eml")
    assert r.status_code == 200 and r.headers["content-type"].startswith("message/rfc822")
    assert (
        "attachment" in r.headers["content-disposition"]
        and ".eml" in r.headers["content-disposition"]
    )
    msg = message_from_bytes(r.content, policy=policy.default)
    assert msg["X-Unsent"] == "1"  # opens in Outlook as a draft for a person to send
    assert msg["Subject"] == "Daily Regulatory Update – 20.11.2026 (EOD)"
    assert "tvscs-compliance@example.invalid" in msg["To"]
    assert "Consent Manager Registration Framework" in msg.get_body(("html",)).get_content()
    html = client.get(f"/api/v1/daily-update/{update_id}/export.html")
    assert html.status_code == 200 and html.headers["content-type"].startswith("text/html")
    assert preview(client).json()["status"] == "EXPORTED"


def test_export_roles_and_404(client) -> None:
    assert as_user(client, AUDIT).get("/api/v1/daily-update/x/export.eml").status_code == 403
    assert as_user(client, DIVYA).get("/api/v1/daily-update/x/export.eml").status_code == 404


def test_the_system_has_no_way_to_send_email() -> None:
    import pathlib
    import re

    app_dir = pathlib.Path(__file__).resolve().parents[2] / "app"
    for path in app_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        assert not re.search(r"^\s*(import|from)\s+(smtplib|aiosmtplib|exchangelib)", text, re.M), (
            path
        )
        assert "sendmail" not in text and "send_message" not in text, path
