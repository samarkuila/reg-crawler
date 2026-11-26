"""Deleting a source: proposed, approved by a different approver, history kept."""

from tests.api.conftest import DIVYA, GEORGE, SAMARENDRA, as_user


def _first_active(client) -> str:
    views = as_user(client, SAMARENDRA).get("/api/v1/sources").json()
    return next(
        v["source"]["id"] for v in views if v["source"]["active"] and not v["pending_version"]
    )


def test_delete_needs_a_second_person_and_keeps_history(client, audit_count):
    sid = _first_active(client)
    before = audit_count()
    r = as_user(client, SAMARENDRA).delete(f"/api/v1/sources/{sid}")
    assert r.status_code == 202 and r.json()["after"] == {"retired": True, "active": False}
    assert audit_count() > before
    still = {v["source"]["id"]: v for v in client.get("/api/v1/sources").json()}
    assert sid in still and still[sid]["source"]["active"] is True  # nothing changes yet

    version = r.json()["version"]
    own = as_user(client, SAMARENDRA).post(f"/api/v1/sources/{sid}/versions/{version}/approve")
    assert own.status_code == 403  # not the person who proposed it
    ok = as_user(client, GEORGE).post(f"/api/v1/sources/{sid}/versions/{version}/approve")
    assert ok.status_code == 200

    ids = {v["source"]["id"] for v in client.get("/api/v1/sources").json()}
    assert sid not in ids  # gone from the register
    assert any(h["source_id"] == sid for h in client.get(f"/api/v1/sources/{sid}/versions").json())


def test_delete_is_refused_for_the_wrong_role_and_for_pending_or_repeat(client):
    sid = _first_active(client)
    assert as_user(client, DIVYA).delete(f"/api/v1/sources/{sid}").status_code == 403
    assert as_user(client, SAMARENDRA).delete("/api/v1/sources/nope").status_code == 404
    first = as_user(client, SAMARENDRA).delete(f"/api/v1/sources/{sid}")
    assert first.status_code == 202
    assert as_user(client, SAMARENDRA).delete(f"/api/v1/sources/{sid}").status_code == 409
    version = first.json()["version"]
    as_user(client, GEORGE).post(f"/api/v1/sources/{sid}/versions/{version}/approve")
    assert as_user(client, SAMARENDRA).delete(f"/api/v1/sources/{sid}").status_code == 409


def test_a_new_source_can_reuse_a_deleted_name(client):
    sid = _first_active(client)
    view = next(v for v in client.get("/api/v1/sources").json() if v["source"]["id"] == sid)
    s = view["source"]
    version = as_user(client, SAMARENDRA).delete(f"/api/v1/sources/{sid}").json()["version"]
    as_user(client, GEORGE).post(f"/api/v1/sources/{sid}/versions/{version}/approve")
    body = {k: s[k] for k in ("authority", "page_name", "type", "url", "rss_url", "adapter_config")}
    body["id"] = None
    if body["type"] == "HTML_LIST":  # an HTML listing needs selectors and a date format
        body["adapter_config"] = {
            "selectors": {"row": "tr", "title": "a", "link": "a"},
            "date_format": "%d.%m.%Y",
        }
    r = as_user(client, SAMARENDRA).post("/api/v1/sources", json=body)
    assert r.status_code == 201, r.text
    assert r.json()["source"]["id"] != sid
