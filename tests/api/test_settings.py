from tests.api.conftest import AUDIT, DIVYA, GEORGE, SAMARENDRA, as_user


def get(client, key):
    return client.get(f"/api/v1/settings/{key}")


def test_settings_are_seeded_as_data(client) -> None:
    keys = {s["setting"]["key"] for s in client.get("/api/v1/settings").json()}
    assert {
        "run-times",
        "working-days",
        "holidays",
        "lookback-days",
        "dismiss-reasons",
        "exclusion-keywords",
        "relevance-keywords",
        "low-relevance-threshold",
        "screening-guidance",
    } <= keys
    run_times = get(client, "run-times").json()["setting"]["value"]
    assert run_times["morning"] == "10:00" and run_times["eod"] == "19:30"
    assert get(client, "lookback-days").json()["setting"]["value"] == 2


def test_unknown_setting_404(client) -> None:
    assert get(client, "nope").status_code == 404
    assert (
        as_user(client, SAMARENDRA).put("/api/v1/settings/nope", json={"value": 1}).status_code
        == 404
    )


def test_change_is_pending_until_a_different_approver_approves(client, audit_count) -> None:
    before = audit_count()
    r = as_user(client, SAMARENDRA).put("/api/v1/settings/lookback-days", json={"value": 3})
    assert r.status_code == 200
    body = r.json()
    assert body["has_pending"] is True and body["setting"]["value"] == 2  # live value unchanged
    assert body["setting"]["pending"]["value"] == 3 and body["setting"]["pending"]["version"] == 2
    assert audit_count() == before + 1
    assert (
        as_user(client, SAMARENDRA).post("/api/v1/settings/lookback-days/approve").status_code
        == 403
    )
    assert as_user(client, DIVYA).post("/api/v1/settings/lookback-days/approve").status_code == 403
    done = as_user(client, GEORGE).post("/api/v1/settings/lookback-days/approve")
    assert done.status_code == 200
    s = done.json()["setting"]
    assert s["value"] == 3 and s["version"] == 2 and s["pending"] is None
    assert audit_count() == before + 2
    assert client.post("/api/v1/settings/lookback-days/approve").status_code == 409


def test_approver_cannot_approve_their_own_setting_change(client) -> None:
    as_user(client, GEORGE).put("/api/v1/settings/lookback-days", json={"value": 4})
    r = client.post("/api/v1/settings/lookback-days/approve")
    assert r.status_code == 403 and "different person" in r.json()["detail"]
    assert get(client, "lookback-days").json()["setting"]["value"] == 2


def test_reject_discards_the_pending_value(client) -> None:
    as_user(client, SAMARENDRA).put("/api/v1/settings/holidays", json={"value": ["25.12.2026"]})
    url = "/api/v1/settings/holidays/reject"
    assert as_user(client, GEORGE).post(url, json={}).status_code == 422
    r = client.post(url, json={"reason": "Not confirmed"})
    assert r.status_code == 200 and r.json()["has_pending"] is False
    assert r.json()["setting"]["value"] == []
    assert client.post(url, json={"reason": "again"}).status_code == 409


def test_value_must_match_the_settings_type(client) -> None:
    c = as_user(client, SAMARENDRA)
    assert c.put("/api/v1/settings/lookback-days", json={"value": "three"}).status_code == 422
    assert c.put("/api/v1/settings/lookback-days", json={"value": True}).status_code == 422
    assert c.put("/api/v1/settings/lookback-days", json={"value": 99}).status_code == 422
    assert c.put("/api/v1/settings/low-relevance-threshold", json={"value": 101}).status_code == 422
    assert c.put("/api/v1/settings/holidays", json={"value": "25.12.2026"}).status_code == 422
    assert c.put("/api/v1/settings/lookback-days", json={}).status_code == 422
    assert c.put("/api/v1/settings/lookback-days", json={"value": 2, "x": 1}).status_code == 422


def test_only_admin_or_approver_can_propose(client) -> None:
    for user in (DIVYA, AUDIT):
        r = as_user(client, user).put("/api/v1/settings/lookback-days", json={"value": 3})
        assert r.status_code == 403, user


def test_threshold_change_moves_items_between_sections_only_after_approval(client) -> None:
    def low():
        items = client.get("/api/v1/items").json()["items"]
        return {i["id"] for i in items if i["section"] == "low_relevance"}

    assert len(low()) == 3
    as_user(client, SAMARENDRA).put("/api/v1/settings/low-relevance-threshold", json={"value": 55})
    assert len(low()) == 3  # unapproved change is not used
    as_user(client, GEORGE).post("/api/v1/settings/low-relevance-threshold/approve")
    assert "item-irdai-agents" in low()  # score 52 is now below the threshold
    assert len(client.get("/api/v1/items").json()["items"]) == 10  # still nothing hidden
