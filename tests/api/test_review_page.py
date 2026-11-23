"""Daily review page: shell, static assets, and the actions the page performs (each is audited)."""

from tests.api.conftest import ACCEPT_IORS, DIVYA, EGAZETTE_CHECK, as_user

IORS = "item-iors-faqs"
MANUAL = "item-rbi-pdf-blocked"
LOW = ["item-rbi-mmo", "item-rbi-auction", "item-trai-subs"]


def _audit(client) -> list[dict]:
    return client.get("/api/v1/audit").json()


def test_review_page_renders_shell_and_script(client) -> None:
    html = client.get("/review").text
    for marker in (
        'id="rv-health"', 'id="rv-counts"', 'id="rv-rows"', 'id="rv-manual"', 'id="rv-low"',
        'id="rv-panel"', "/static/js/pages/review.js", 'id="f-auth"', 'id="f-q"',
    ):
        assert marker in html, marker
    assert "not built yet" not in html
    assert client.get("/static/js/pages/review.js").status_code == 200


def test_items_carry_plain_english_state_label(client) -> None:
    labels = {i["id"]: i["state_label"] for i in client.get("/api/v1/items").json()["items"]}
    assert labels[MANUAL] == "Needs manual fetch" and labels[IORS] in {"Pending", "In review"}


def test_sme_functions_for_review_form(client) -> None:
    body = client.get("/api/v1/sme-functions").json()
    assert {"id": "treasury", "name": "Treasury"} in body
    assert all(set(f) == {"id", "name"} for f in body)


def test_accept_iors_is_audited_and_records_edits(client, audit_count) -> None:
    as_user(client, DIVYA)
    before = audit_count()
    body = {**ACCEPT_IORS, "authority": "RBI (edited)"}
    r = client.post(f"/api/v1/items/{IORS}/decision", json=body)
    assert r.status_code == 201
    assert r.json()["decision"]["fields_edited"]["authority"]["final"] == "RBI (edited)"
    assert audit_count() > before
    assert any(e["entity_id"] == IORS or IORS in str(e["after"]) for e in _audit(client))


def test_bulk_dismiss_low_relevance_is_audited(client, audit_count) -> None:
    as_user(client, DIVYA)
    before = audit_count()
    r = client.post("/api/v1/items/bulk-dismiss", json={"item_ids": LOW, "reason": "Not relevant"})
    assert r.status_code == 200 and r.json()["dismissed"] == LOW
    assert audit_count() > before
    items = {i["id"]: i for i in client.get("/api/v1/items").json()["items"]}
    assert all(items[i]["decided"] for i in LOW)  # still listed, never deleted


def test_upload_and_manual_check_are_audited(client, audit_count) -> None:
    as_user(client, DIVYA)
    before = audit_count()
    r = client.post(
        f"/api/v1/items/{MANUAL}/upload",
        files={"file": ("circular.pdf", b"%PDF-1.4 x", "application/pdf")},
    )
    assert r.status_code == 201 and r.json()["uploaded_by"]
    after_upload = audit_count()
    assert after_upload > before
    url = f"/api/v1/source-checks/{EGAZETTE_CHECK}/manual-check"
    r = client.post(url, json={"note": "Checked"})
    assert r.status_code == 201
    assert audit_count() > after_upload
