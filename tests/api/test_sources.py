from tests.api.conftest import AUDIT, DIVYA, GEORGE, SAMARENDRA, as_user

NEW_SOURCE = {
    "id": "npci-nach",
    "authority": "NPCI",
    "page_name": "NACH circulars",
    "type": "HTML_LIST",
    "url": "https://www.npci.org.in/circulars",
    # An HTML listing needs its row, title and link selectors and a date format.
    "adapter_config": {
        "selectors": {"row": "tr.item", "title": "td a", "link": "td a"},
        "date_format": "%d.%m.%Y",
    },
}


def sources(client):
    return {s["source"]["id"]: s for s in client.get("/api/v1/sources").json()}


def test_register_has_five_poc_sources_and_eight_planned(client) -> None:
    rows = client.get("/api/v1/sources").json()
    assert len(rows) == 13
    active = [r for r in rows if r["source"]["active"]]
    planned = [r for r in rows if not r["source"]["active"]]
    assert {r["source"]["authority"] for r in active} == {
        "RBI",
        "UNSC",
        "TRAI",
        "IRDAI",
        "eGazette",
    }
    assert len(planned) == 8 and all(r["source"]["planned"] for r in planned)
    assert {r["status_label"] for r in planned} == {"Planned (V2)"}
    assert [r["source"]["id"] for r in rows[:5]] == ["egazette", "irdai", "rbi", "trai", "unsc"]
    assert sources(client)["unsc"]["source"]["priority"] == "URGENT"


def test_rbi_covers_all_tabs(client) -> None:
    tabs = sources(client)["rbi"]["source"]["adapter_config"]["tabs"]
    assert "Master Directions" in tabs and "FAQs" in tabs and "What's New" in tabs


def test_pending_version_for_egazette(client) -> None:
    row = sources(client)["egazette"]
    pending = row["pending_version"]
    assert pending["version"] == 3 and pending["status"] == "PENDING"
    assert pending["requested_by"] == "Samarendra K."
    before = pending["before"]["adapter_config"]["ministries"]
    after = pending["after"]["adapter_config"]["ministries"]
    assert set(after) - set(before) == {"Electronics and IT", "Labour and Employment"}
    assert row["source"]["current_version"] == 2  # the pending version is not live yet
    versions = client.get("/api/v1/sources/egazette/versions").json()
    assert [v["version"] for v in versions] == [1, 2, 3]


def test_requester_cannot_approve_their_own_change(client) -> None:
    r = as_user(client, SAMARENDRA).post("/api/v1/sources/egazette/versions/3/approve")
    assert r.status_code == 403  # admin role cannot approve at all
    # even an approver who made the request cannot approve it
    as_user(client, GEORGE).post("/api/v1/sources/trai/versions", json={"priority": "URGENT"})
    r = client.post("/api/v1/sources/trai/versions/2/approve")
    assert r.status_code == 403 and "different person" in r.json()["detail"]
    assert sources(client)["trai"]["source"]["priority"] == "NORMAL"


def test_a_different_approver_applies_the_version(client, audit_count) -> None:
    before = audit_count()
    r = as_user(client, GEORGE).post("/api/v1/sources/egazette/versions/3/approve")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "APPROVED" and body["approved_by"] == "George K."
    live = sources(client)["egazette"]
    assert live["source"]["current_version"] == 3 and live["pending_version"] is None
    assert "Labour and Employment" in live["source"]["adapter_config"]["ministries"]
    assert audit_count() == before + 2  # version approved + source updated
    assert client.post("/api/v1/sources/egazette/versions/3/approve").status_code == 409


def test_reject_needs_reason_and_leaves_source_unchanged(client) -> None:
    url = "/api/v1/sources/egazette/versions/3/reject"
    assert as_user(client, GEORGE).post(url, json={}).status_code == 422
    assert client.post(url, json={"reason": "  "}).status_code == 422
    assert as_user(client, DIVYA).post(url, json={"reason": "no"}).status_code == 403
    r = as_user(client, GEORGE).post(url, json={"reason": "Wrong ministries"})
    assert r.status_code == 200 and r.json()["status"] == "REJECTED"
    assert r.json()["reject_reason"] == "Wrong ministries"
    live = sources(client)["egazette"]
    assert live["source"]["current_version"] == 2 and live["pending_version"] is None


def test_propose_change_creates_pending_version_with_before_and_after(client, audit_count) -> None:
    before = audit_count()
    r = as_user(client, SAMARENDRA).post(
        "/api/v1/sources/trai/versions",
        json={"include_keywords": ["NBFC"], "priority": "URGENT"},
    )
    assert r.status_code == 201
    v = r.json()
    assert v["version"] == 2 and v["status"] == "PENDING" and v["requested_by"] == "Samarendra K."
    assert v["before"] == {"include_keywords": [], "priority": "NORMAL"}
    assert v["after"] == {"include_keywords": ["NBFC"], "priority": "URGENT"}
    assert audit_count() == before + 1
    live = sources(client)["trai"]["source"]
    assert live["priority"] == "NORMAL" and live["current_version"] == 1  # not live until approved
    as_user(client, GEORGE).post("/api/v1/sources/trai/versions/2/approve")
    assert sources(client)["trai"]["source"]["priority"] == "URGENT"


def test_propose_validation_and_roles(client) -> None:
    url = "/api/v1/sources/trai/versions"
    assert as_user(client, SAMARENDRA).post(url, json={}).status_code == 422  # nothing changes
    assert client.post(url, json={"priority": "NORMAL"}).status_code == 422  # same as now
    assert client.post(url, json={"priority": "SOON"}).status_code == 422
    assert client.post(url, json={"colour": "red"}).status_code == 422
    assert as_user(client, DIVYA).post(url, json={"priority": "URGENT"}).status_code == 403
    assert as_user(client, AUDIT).post(url, json={"priority": "URGENT"}).status_code == 403
    assert (
        client.post("/api/v1/sources/nope/versions", json={"priority": "URGENT"}).status_code == 403
    )
    assert (
        as_user(client, SAMARENDRA)
        .post("/api/v1/sources/nope/versions", json={"priority": "URGENT"})
        .status_code
        == 404
    )
    assert client.get("/api/v1/sources/nope/versions").status_code == 404


def test_add_source_starts_pending_and_inactive(client) -> None:
    r = as_user(client, SAMARENDRA).post("/api/v1/sources", json=NEW_SOURCE)
    assert r.status_code == 201
    body = r.json()
    assert body["source"]["active"] is False and body["source"]["current_version"] == 0
    assert body["status_label"] == "Waiting for approval"
    assert body["pending_version"]["version"] == 1
    assert (
        as_user(client, GEORGE).post("/api/v1/sources/npci-nach/versions/1/approve").status_code
        == 200
    )
    live = sources(client)["npci-nach"]
    assert live["source"]["active"] is True and live["status_label"] == "Active"


def test_add_source_validation_and_roles(client) -> None:
    assert (
        as_user(client, SAMARENDRA)
        .post("/api/v1/sources", json={**NEW_SOURCE, "id": "NPCI Nach"})
        .status_code
        == 422
    )
    assert client.post("/api/v1/sources", json={**NEW_SOURCE, "type": "FTP"}).status_code == 422
    assert client.post("/api/v1/sources", json={**NEW_SOURCE, "id": "rbi"}).status_code == 409
    assert as_user(client, DIVYA).post("/api/v1/sources", json=NEW_SOURCE).status_code == 403
    assert as_user(client, AUDIT).post("/api/v1/sources", json=NEW_SOURCE).status_code == 403


def test_planned_source_can_be_set_up_through_a_version(client) -> None:
    as_user(client, SAMARENDRA).post("/api/v1/sources/npci/versions", json={"active": True})
    as_user(client, GEORGE).post("/api/v1/sources/npci/versions/1/approve")
    live = sources(client)["npci"]["source"]
    assert live["active"] is True and live["planned"] is False


def test_test_source_dry_run_saves_nothing(client, audit_count) -> None:
    before = audit_count()
    r = as_user(client, SAMARENDRA).post("/api/v1/sources/trai/test")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "OK" and len(body["rows"]) == 3 and body["saved"] is False
    assert audit_count() == before  # a dry run changes no state
    assert client.get("/api/v1/items", params={"q": "TRAI sample row"}).json()["items"] == []


def test_test_source_reports_blocked_without_trying_to_get_around_it(client) -> None:
    body = as_user(client, SAMARENDRA).post("/api/v1/sources/egazette/test").json()
    assert body["status"] == "BLOCKED" and body["rows"] == []
    assert "verification page" in body["message"]


def test_test_source_roles_and_404(client) -> None:
    assert as_user(client, AUDIT).post("/api/v1/sources/trai/test").status_code == 403
    assert as_user(client, DIVYA).post("/api/v1/sources/trai/test").status_code == 403
    assert as_user(client, GEORGE).post("/api/v1/sources/trai/test").status_code == 200
    assert client.post("/api/v1/sources/nope/test").status_code == 404
