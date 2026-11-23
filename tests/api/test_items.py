import hashlib

from tests.api.conftest import ACCEPT_IORS, AUDIT, DIVYA, EOD, GEORGE, SAMARENDRA, as_user

IORS = "item-iors-faqs"
MANUAL = "item-rbi-pdf-blocked"


def _items(client, **params):
    return client.get("/api/v1/items", params=params).json()


def test_queue_defaults_to_latest_cycle_and_never_hides_items(client) -> None:
    body = _items(client)
    assert body["cycle"] == EOD
    assert len(body["items"]) == 10  # nothing is filtered out, low relevance included
    counts = body["counts"]
    assert counts["urgent"] == 1 and counts["modified"] == 1 and counts["backdated"] == 1
    assert counts["needs_manual_fetch"] == 1 and counts["decided"] == 2
    assert counts["total"] == 10 and counts["low_relevance"] == 3


def test_queue_sorted_urgent_first_then_relevance(client) -> None:
    items = _items(client)["items"]
    assert items[0]["priority"] == "URGENT" and items[0]["authority"] == "UNSC"
    rest = [i["relevance_score"] for i in items[1:]]
    assert rest == sorted(rest, reverse=True)


def test_queue_sections_and_badges(client) -> None:
    by_id = {i["id"]: i for i in _items(client)["items"]}
    assert by_id[MANUAL]["section"] == "manual_fetch"
    low = {i for i, v in by_id.items() if v["section"] == "low_relevance"}
    assert low == {"item-rbi-mmo", "item-rbi-auction", "item-trai-subs"}
    assert by_id[IORS]["badges"] == ["MODIFIED"] and by_id[IORS]["channel_label"] == "RSS + HTML"
    assert by_id["item-unsc-amend"]["badges"][:2] == ["URGENT", "NEW"]
    assert by_id["item-unsc-amend"]["channel_label"] == "XML"
    backdated = by_id["item-rbi-backdated"]
    assert "BACKDATED" in backdated["badges"]
    assert backdated["note"] == "Issued 19.11.2026 · published 20.11.2026"
    rss = by_id["item-rbi-rss-only"]
    assert "RSS_ONLY" in rss["badges"] and rss["channel_mismatch"] is True
    assert by_id["item-irdai-agents"]["decided"] is True


def test_queue_filters(client) -> None:
    assert [i["id"] for i in _items(client, state="NEEDS_MANUAL_FETCH")["items"]] == [MANUAL]
    assert {i["channel"] for i in _items(client, channel="RSS")["items"]} == {"RSS"}
    assert [i["id"] for i in _items(client, q="sandbox")["items"]] == [IORS]
    assert [i["id"] for i in _items(client, q="2336151")["items"]] == ["item-pib-2336151"]
    assert all(i["relevance_score"] >= 80 for i in _items(client, min_relevance=80)["items"])
    # tiles describe the whole cycle even when the list is filtered
    assert _items(client, q="sandbox")["counts"]["total"] == 10


def test_queue_other_cycle_and_bad_input(client) -> None:
    morning = _items(client, cycle="2026-11-20-MORNING")
    assert morning["items"] == [] and morning["counts"]["total"] == 0
    assert client.get("/api/v1/items", params={"cycle": "x"}).status_code == 422
    assert client.get("/api/v1/items", params={"state": "BOGUS"}).status_code == 422
    assert client.get("/api/v1/items", params={"min_relevance": 101}).status_code == 422


def test_item_detail_has_prefill_and_traceability_chain(client) -> None:
    body = client.get(f"/api/v1/items/{IORS}").json()
    assert body["item"]["internal_ref"] == "TVSC-REG-2026-000214"
    assert (
        body["prefill"]["ref_no_and_date"]
        == "Inter-operable Regulatory Sandbox: IoRS – FAQs · Date: 17.11.2026 (Updated)"
    )
    assert body["prefill"]["effective_date"] == "2026-11-17"
    assert "issue date" in body["prefill"]["effective_date_rule"]
    kinds = [s["kind"] for s in body["chain"]]
    assert kinds == ["document", "detection", "detection", "snapshot", "source_version", "run"]
    assert body["documents"][0]["sha256"] and body["snapshots"][0]["sha256"]
    mod = [o for o in body["observations"] if o["change_type"] == "MODIFIED"][0]
    assert mod["prior_hash"] != mod["current_hash"]


def test_item_detail_for_a_decided_item_links_the_email(client) -> None:
    body = client.get("/api/v1/items/item-pib-2336151").json()
    kinds = [s["kind"] for s in body["chain"]]
    assert kinds[0] == "email" and "decision" in kinds
    assert body["latest_decision"]["reviewer"] == "Divya G."


def test_backdated_prefill_shows_both_dates(client) -> None:
    prefill = client.get("/api/v1/items/item-rbi-backdated").json()["prefill"]
    assert prefill["ref_no_and_date"] == (
        "RBI/2026-27/118 dated 19.11.2026 · Published on website: 20.11.2026"
    )
    assert prefill["issue_date"] == "2026-11-19" and prefill["publish_date"] == "2026-11-20"


def test_unsc_effective_date_rule(client) -> None:
    prefill = client.get("/api/v1/items/item-unsc-amend").json()["prefill"]
    assert "publish date" in prefill["effective_date_rule"]


def test_item_detail_404(client) -> None:
    assert client.get("/api/v1/items/nope").status_code == 404


def test_accept_records_decision_state_and_audit(client, audit_count) -> None:
    before = audit_count()
    r = as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    assert r.status_code == 201
    body = r.json()
    assert body["item"]["state"] == "DECIDED" and body["item"]["decided"] is True
    d = body["decision"]
    assert d["reviewer"] == "Divya G." and d["is_draft"] is False
    assert d["authority"] == "RBI"  # pre-filled from the source
    assert d["fields_edited"] == {}  # nothing pre-filled was changed
    assert audit_count() == before + 2  # the decision and the state change, same transaction


def test_accept_tracks_which_prefilled_fields_were_edited(client) -> None:
    body = {**ACCEPT_IORS, "effective_date": "2026-11-30", "guideline_title": "IoRS FAQs"}
    d = as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=body).json()["decision"]
    assert set(d["fields_edited"]) == {"effective_date", "guideline_title"}
    assert d["fields_edited"]["effective_date"] == {
        "prefilled": "2026-11-17",
        "final": "2026-11-30",
    }


def test_save_keeps_a_draft_and_does_not_reach_the_email(client) -> None:
    body = {**ACCEPT_IORS, "action": "save"}
    r = as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=body).json()
    assert r["decision"]["is_draft"] is True and r["item"]["state"] == "IN_REVIEW"
    rows = client.get("/api/v1/daily-update/preview", params={"cycle": EOD}).json()["rows"]
    assert [x["item_id"] for x in rows] == ["item-pib-2336151"]


def test_save_on_a_decided_item_does_not_reopen_it(client) -> None:
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    r = client.post(f"/api/v1/items/{IORS}/decision", json={**ACCEPT_IORS, "action": "save"}).json()
    assert r["item"]["state"] == "DECIDED"


def test_dismiss_needs_a_reason_and_keeps_the_item(client, audit_count) -> None:
    url = "/api/v1/items/item-trai-subs/decision"
    assert as_user(client, DIVYA).post(url, json={"action": "dismiss"}).status_code == 422
    assert client.post(url, json={"action": "dismiss", "dismiss_reason": "  "}).status_code == 422
    before = audit_count()
    r = client.post(url, json={"action": "dismiss", "dismiss_reason": "Statistics only"})
    assert r.status_code == 201
    assert r.json()["decision"]["treatment"] == "NOT_RELEVANT"
    assert r.json()["decision"]["include_in_update"] is False
    assert audit_count() == before + 2
    # dismissed, never deleted
    ids = [i["id"] for i in _items(client)["items"]]
    assert "item-trai-subs" in ids


def test_accept_validation(client) -> None:
    url = f"/api/v1/items/{IORS}/decision"
    c = as_user(client, DIVYA)
    assert c.post(url, json={"action": "accept"}).status_code == 422  # no treatment
    assert c.post(url, json={"action": "accept", "treatment": "NOT_RELEVANT"}).status_code == 422
    assert (
        c.post(url, json={**ACCEPT_IORS, "impact_text": ""}).status_code == 422
    )  # included, no impact
    assert c.post(url, json={**ACCEPT_IORS, "treatment": "OTHER_FUNCTION"}).status_code == 422
    bad_sme = {**ACCEPT_IORS, "treatment": "OTHER_FUNCTION", "sme_function": "nope"}
    assert c.post(url, json=bad_sme).status_code == 422
    assert c.post(url, json={**ACCEPT_IORS, "action": "other"}).status_code == 422
    assert c.post(url, json={**ACCEPT_IORS, "surprise": 1}).status_code == 422  # unknown field
    ok = {**ACCEPT_IORS, "treatment": "OTHER_FUNCTION", "sme_function": "treasury"}
    assert c.post(url, json=ok).status_code == 201


def test_decision_roles(client) -> None:
    url = f"/api/v1/items/{IORS}/decision"
    assert as_user(client, AUDIT).post(url, json=ACCEPT_IORS).status_code == 403
    assert as_user(client, SAMARENDRA).post(url, json=ACCEPT_IORS).status_code == 403
    assert as_user(client, GEORGE).post(url, json=ACCEPT_IORS).status_code == 201
    assert client.post("/api/v1/items/nope/decision", json=ACCEPT_IORS).status_code == 404


def test_manual_fetch_item_needs_the_document_before_review(client) -> None:
    url = f"/api/v1/items/{MANUAL}/decision"
    assert as_user(client, DIVYA).post(url, json=ACCEPT_IORS).status_code == 409
    assert client.post(url, json={**ACCEPT_IORS, "action": "save"}).status_code == 409
    assert (
        client.post(url, json={"action": "dismiss", "dismiss_reason": "Not needed"}).status_code
        == 201
    )


def test_bulk_dismiss_with_one_reason(client, audit_count) -> None:
    ids = ["item-rbi-mmo", "item-rbi-auction", "item-trai-subs"]
    before = audit_count()
    r = as_user(client, DIVYA).post(
        "/api/v1/items/bulk-dismiss",
        json={"item_ids": ids, "reason": "Market operations – not NBFC"},
    )
    assert r.status_code == 200 and r.json()["dismissed"] == ids
    assert audit_count() == before + 6  # decision + state change for each item
    by_id = {i["id"]: i for i in _items(client)["items"]}
    assert all(by_id[i]["state"] == "DECIDED" for i in ids)
    reasons = {
        client.get(f"/api/v1/items/{i}").json()["latest_decision"]["dismiss_reason"] for i in ids
    }
    assert reasons == {"Market operations – not NBFC"}


def test_bulk_dismiss_is_all_or_nothing(client, audit_count) -> None:
    before = audit_count()
    r = as_user(client, DIVYA).post(
        "/api/v1/items/bulk-dismiss", json={"item_ids": ["item-rbi-mmo", "nope"], "reason": "x"}
    )
    assert r.status_code == 404
    assert audit_count() == before
    assert _items(client, q="money market")["items"][0]["state"] == "DETECTED"


def test_bulk_dismiss_validation_and_roles(client) -> None:
    url = "/api/v1/items/bulk-dismiss"
    assert as_user(client, DIVYA).post(url, json={"item_ids": [], "reason": "x"}).status_code == 422
    assert client.post(url, json={"item_ids": ["item-rbi-mmo"], "reason": " "}).status_code == 422
    assert (
        as_user(client, AUDIT)
        .post(url, json={"item_ids": ["item-rbi-mmo"], "reason": "x"})
        .status_code
        == 403
    )


def test_manual_upload_stores_hash_and_moves_item_to_review(client, audit_count) -> None:
    data = b"%PDF-1.4 sample press release"
    before = audit_count()
    r = as_user(client, DIVYA).post(
        f"/api/v1/items/{MANUAL}/upload",
        files={"file": ("press release.pdf", data, "application/pdf")},
    )
    assert r.status_code == 201
    doc = r.json()
    assert doc["sha256"] == hashlib.sha256(data).hexdigest()
    assert doc["fetch_status"] == "MANUAL" and doc["uploaded_by"] == "Divya G."
    assert doc["filename"] == "press release.pdf" and doc["mime"] == "application/pdf"
    assert audit_count() == before + 2  # document + state change
    detail = client.get(f"/api/v1/items/{MANUAL}").json()
    assert detail["item"]["state"] == "IN_REVIEW"
    assert {d["fetch_status"] for d in detail["documents"]} == {"BLOCKED", "MANUAL"}  # append-only
    assert client.post(f"/api/v1/items/{MANUAL}/decision", json=ACCEPT_IORS).status_code == 201


def test_manual_upload_rules(client) -> None:
    url = f"/api/v1/items/{MANUAL}/upload"
    files = {"file": ("a.pdf", b"x", "application/pdf")}
    assert as_user(client, AUDIT).post(url, files=files).status_code == 403
    assert (
        as_user(client, DIVYA)
        .post(url, files={"file": ("a.pdf", b"", "application/pdf")})
        .status_code
        == 422
    )
    assert client.post(f"/api/v1/items/{IORS}/upload", files=files).status_code == 409
    assert client.post("/api/v1/items/nope/upload", files=files).status_code == 404
    assert client.post(url).status_code == 422  # no file


def test_upload_filename_cannot_escape_the_documents_folder(client) -> None:
    r = as_user(client, DIVYA).post(
        f"/api/v1/items/{MANUAL}/upload",
        files={"file": ("..\\..\\evil.pdf", b"x", "application/pdf")},
    )
    doc = r.json()
    assert doc["filename"] == "evil.pdf" and ".." not in doc["path"]


def test_decided_by_approver_item_is_published_after_approval_cannot_change(client) -> None:
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    cycle = {"cycle": EOD}
    update = client.get("/api/v1/daily-update/preview", params=cycle).json()
    client.post(
        "/api/v1/source-checks/chk-2026-11-20-eod-egazette-html/manual-check", json={"note": "ok"}
    )
    assert (
        as_user(client, GEORGE).post(f"/api/v1/daily-update/{update['id']}/approve").status_code
        == 200
    )
    assert client.post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS).status_code == 409
    assert (
        client.post(
            "/api/v1/items/bulk-dismiss", json={"item_ids": [IORS], "reason": "x"}
        ).status_code
        == 409
    )
