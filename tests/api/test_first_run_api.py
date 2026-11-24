"""First-crawl window through the API: rebaseline, the origin filter, per-source overrides."""

import pytest

from app.api.deps import get_services
from app.schemas.enums import BaselineStatus, ItemOrigin
from app.services import review as review_module
from app.services.first_run import FirstRunConfig
from tests.api.conftest import DIVYA, GEORGE, SAMARENDRA, as_user

API = "/api/v1"


@pytest.fixture
def hidden_baseline(monkeypatch):
    """The queue leaves first-crawl items out (the live setting may differ)."""
    monkeypatch.setattr(
        review_module,
        "load_first_run_config",
        lambda: FirstRunConfig(baseline_items_in_review=False),
    )


def first_active(client) -> str:
    return next(
        v["source"]["id"] for v in client.get(f"{API}/sources").json() if v["source"]["active"]
    )


def test_rebaseline_is_approver_only_and_audited(client, audit_count) -> None:
    sid = first_active(client)
    assert as_user(client, DIVYA).post(f"{API}/sources/{sid}/rebaseline").status_code == 403
    assert as_user(client, SAMARENDRA).post(f"{API}/sources/{sid}/rebaseline").status_code == 403
    before = audit_count()
    r = as_user(client, GEORGE).post(f"{API}/sources/{sid}/rebaseline")
    assert r.status_code == 200, r.text
    assert r.json()["source"]["baseline_status"] == "NOT_BASELINED"
    assert audit_count() == before + 1
    assert any(e.action == "REBASELINE_REQUESTED" for e in get_services().repo.list_audit())
    # a source that is already waiting for its first crawl: nothing to do
    assert as_user(client, GEORGE).post(f"{API}/sources/{sid}/rebaseline").status_code == 409
    assert as_user(client, GEORGE).post(f"{API}/sources/nope/rebaseline").status_code == 404


def test_a_source_can_carry_first_run_overrides_through_the_versioned_path(client) -> None:
    body = {
        "authority": "OverReg",
        "page_name": "Override page",
        "type": "HTML_LIST",
        "url": "https://overreg.example.gov.in/list",
        "first_run_mode": "last_n_days",
        "first_run_days": 14,
        "sorted_newest_first": False,
    }
    made = as_user(client, SAMARENDRA).post(f"{API}/sources", json=body)
    assert made.status_code == 201, made.text
    source = made.json()["source"]
    assert source["first_run_mode"] == "last_n_days" and source["first_run_days"] == 14
    assert source["sorted_newest_first"] is False
    assert source["baseline_status"] == "NOT_BASELINED"  # not live until approved either
    sid = source["id"]
    assert (
        as_user(client, GEORGE).post(f"{API}/sources/{sid}/versions/1/approve").status_code == 200
    )
    now = next(v for v in client.get(f"{API}/sources").json() if v["source"]["id"] == sid)["source"]
    assert now["first_run_days"] == 14 and now["active"] is True

    bad = {**body, "page_name": "Bad", "first_run_mode": "last_week"}
    assert as_user(client, SAMARENDRA).post(f"{API}/sources", json=bad).status_code == 422
    bad = {**body, "page_name": "Bad2", "first_run_days": 0}
    assert as_user(client, SAMARENDRA).post(f"{API}/sources", json=bad).status_code == 422

    change = {"first_run_days": 30}
    r = as_user(client, SAMARENDRA).post(f"{API}/sources/{sid}/versions", json=change)
    assert r.status_code == 201 and r.json()["after"] == {"first_run_days": 30}


def test_items_can_be_filtered_by_origin(client, hidden_baseline) -> None:
    repo = get_services().repo
    first = repo.list_items(__import__("app.schemas.api", fromlist=["ItemFilter"]).ItemFilter())[0]
    repo.save_detection(first.model_copy(update={"origin": ItemOrigin.BASELINE}), [], False, "t")
    everything = client.get(f"{API}/items").json()
    ids_default = {i["id"] for i in everything["items"]}
    assert first.id not in ids_default  # baseline items stay out of the queue by default
    only = client.get(f"{API}/items", params={"origin": "BASELINE"}).json()
    assert [i["id"] for i in only["items"]] == [first.id]
    assert only["items"][0]["origin"] == "BASELINE"
    crawl_only = client.get(f"{API}/items", params={"origin": "CRAWL"}).json()
    assert first.id not in {i["id"] for i in crawl_only["items"]}
    assert BaselineStatus.BASELINED  # the demo sources are past their first crawl


def test_the_queue_says_how_many_first_crawl_items_it_leaves_out(client, hidden_baseline) -> None:
    repo = get_services().repo
    from app.schemas.api import ItemFilter

    first = repo.list_items(ItemFilter())[0]
    before = client.get(f"{API}/items").json()["baseline_hidden"]
    repo.save_detection(first.model_copy(update={"origin": ItemOrigin.BASELINE}), [], False, "t")
    after = client.get(f"{API}/items").json()
    assert after["baseline_hidden"] == before + 1  # said out loud, not silently dropped
    shown = client.get(f"{API}/items", params={"origin": "BASELINE"}).json()
    assert shown["baseline_hidden"] == 0 and first.id in {i["id"] for i in shown["items"]}
