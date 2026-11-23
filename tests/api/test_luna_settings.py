"""Luna settings: on/off, daily budget and screening guidance are versioned and need approval."""

from tests.api.conftest import GEORGE, SAMARENDRA, as_user

API = "/api/v1/settings"


def propose_and_approve(client, key, value):
    r = as_user(client, SAMARENDRA).put(f"{API}/{key}", json={"value": value})
    assert r.status_code == 200 and r.json()["has_pending"] is True
    done = as_user(client, GEORGE).post(f"{API}/{key}/approve")
    assert done.status_code == 200
    return done.json()["setting"]


def test_the_agent_settings_exist_with_safe_defaults(client) -> None:
    on = client.get(f"{API}/agent-enabled").json()
    budget = client.get(f"{API}/agent-daily-budget-inr").json()
    assert on["setting"]["value"] is True and on["title"] == "Luna on/off"
    assert budget["setting"]["value"] == 200 and "budget" in budget["title"]
    assert (
        "A regulatory update is anything"
        in client.get(f"{API}/screening-guidance").json()["setting"]["value"]["text"]
    )


def test_switching_luna_off_needs_a_different_approver_and_is_versioned(client) -> None:
    before = client.get(f"{API}/agent-enabled").json()["setting"]["version"]
    as_user(client, SAMARENDRA).put(f"{API}/agent-enabled", json={"value": False})
    assert as_user(client, SAMARENDRA).post(f"{API}/agent-enabled/approve").status_code == 403
    done = as_user(client, GEORGE).post(f"{API}/agent-enabled/approve").json()["setting"]
    assert done["value"] is False and done["version"] == before + 1


def test_the_budget_must_be_a_sensible_whole_number(client) -> None:
    def put(value):
        return as_user(client, SAMARENDRA).put(
            f"{API}/agent-daily-budget-inr", json={"value": value}
        )

    assert put(-5).status_code == 422 and put(1_000_000).status_code == 422
    assert put("lots").status_code == 422 and put(True).status_code == 422
    assert put(500).status_code == 200


def test_a_budget_change_is_applied_after_approval(client) -> None:
    assert propose_and_approve(client, "agent-daily-budget-inr", 750)["value"] == 750


def test_screening_guidance_is_versioned_and_needs_approval(client) -> None:
    current = client.get(f"{API}/screening-guidance").json()["setting"]
    new = {**current["value"], "text": current["value"]["text"] + " Capture all DPDP items."}
    r = as_user(client, SAMARENDRA).put(f"{API}/screening-guidance", json={"value": new})
    assert r.status_code == 200
    # until a different person approves it, the old guidance is what Luna reads
    assert (
        client.get(f"{API}/screening-guidance").json()["setting"]["value"]["text"]
        == current["value"]["text"]
    )
    approved = as_user(client, GEORGE).post(f"{API}/screening-guidance/approve").json()["setting"]
    assert approved["value"]["text"].endswith("Capture all DPDP items.")
    assert approved["version"] == current["version"] + 1
