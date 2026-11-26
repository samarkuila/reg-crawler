"""Fetch with: Standard / Browser. Validation, maker-checker, and what the people see."""

import pytest

from app.api.deps import get_services
from tests.api.conftest import GEORGE, SAMARENDRA, as_user

SELECTORS = {"row": "tr.item", "title": "td a", "link": "td a"}
HTML = {
    "authority": "NPCI",
    "page_name": "Engine test",
    "type": "HTML_LIST",
    "url": "https://www.npci.org.in/circulars",
    "adapter_config": {"selectors": SELECTORS, "date_format": "%d.%m.%Y"},
}


def create(client, **changes):
    return as_user(client, SAMARENDRA).post("/api/v1/sources", json={**HTML, **changes})


def source(client, sid):
    views = client.get("/api/v1/sources").json()
    return next(v["source"] for v in views if v["source"]["id"] == sid)


def test_a_browser_html_listing_is_saved_with_its_wait_settings_and_needs_approval(client) -> None:
    r = create(
        client, fetch_engine="browser", wait_for_selector="div.loaded", wait_timeout_s=30
    )
    assert r.status_code == 201, r.text
    sid = r.json()["source"]["id"]
    after = client.get(f"/api/v1/sources/{sid}/versions").json()[0]["after"]
    assert after["fetch_engine"] == "browser" and after["wait_for_selector"] == "div.loaded"
    assert after["wait_timeout_s"] == 30 and "requires_js" not in after
    assert source(client, sid)["active"] is False  # not live until a different person approves
    approve = as_user(client, GEORGE).post(f"/api/v1/sources/{sid}/versions/1/approve")
    assert approve.status_code == 200
    live = source(client, sid)
    assert live["fetch_engine"] == "browser" and live["active"] is True
    # the register says how it is fetched, in plain words
    view = next(v for v in client.get("/api/v1/sources").json() if v["source"]["id"] == sid)
    assert "browser" in view["how_we_read_it"]


def test_the_standard_engine_is_the_default(client) -> None:
    sid = create(client).json()["source"]["id"]
    s = source(client, sid)
    assert s["fetch_engine"] == "http" and s["wait_for_selector"] is None
    assert s["wait_timeout_s"] == 15


@pytest.mark.parametrize("kind", ["RSS", "STRUCTURED_DATA"])
def test_browser_is_not_allowed_for_an_rss_feed_or_a_data_file(client, kind) -> None:
    r = create(client, type=kind, fetch_engine="browser", adapter_config={})
    assert r.status_code == 422
    assert "only available for an HTML listing" in r.json()["detail"]


def test_the_old_browser_way_of_reading_is_refused_for_new_sources(client) -> None:
    r = create(client, type="BROWSER")
    assert r.status_code == 422 and "Fetch with" in r.json()["detail"]


@pytest.mark.parametrize("engine", ["http", "browser"])
@pytest.mark.parametrize("missing", ["row", "title", "link", "date_format"])
def test_an_html_listing_needs_selectors_and_a_date_format_for_both_engines(
    client, engine, missing
) -> None:
    cfg = {"selectors": dict(SELECTORS), "date_format": "%d.%m.%Y"}
    if missing == "date_format":
        del cfg["date_format"]
    else:
        del cfg["selectors"][missing]
    r = create(client, fetch_engine=engine, adapter_config=cfg)
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert "needs its" in detail and missing.replace("_", " ") in detail


@pytest.mark.parametrize("seconds", [0, 61, -5])
def test_wait_timeout_must_be_between_1_and_60_seconds(client, seconds) -> None:
    r = create(client, fetch_engine="browser", wait_timeout_s=seconds)
    assert r.status_code == 422


@pytest.mark.parametrize("seconds", [1, 60])
def test_wait_timeout_limits_are_allowed(client, seconds) -> None:
    assert create(client, fetch_engine="browser", wait_timeout_s=seconds).status_code == 201


def test_a_wait_selector_that_is_not_valid_css_is_refused(client) -> None:
    r = create(client, fetch_engine="browser", wait_for_selector="div[[")
    assert r.status_code == 422 and "not a valid selector" in r.json()["detail"]


def test_the_old_requires_js_field_is_gone_from_the_api(client) -> None:
    assert create(client, requires_js=True).status_code == 422


def test_changing_the_engine_goes_through_a_version(client) -> None:
    sid = create(client).json()["source"]["id"]
    as_user(client, GEORGE).post(f"/api/v1/sources/{sid}/versions/1/approve")
    r = as_user(client, SAMARENDRA).post(
        f"/api/v1/sources/{sid}/versions", json={"fetch_engine": "browser"}
    )
    assert r.status_code == 201, r.text
    assert r.json()["before"] == {"fetch_engine": "http"}
    assert source(client, sid)["fetch_engine"] == "http"  # not live yet
    labels = [c["label"] for c in next(
        v for v in client.get("/api/v1/sources").json() if v["source"]["id"] == sid
    )["pending_changes"]]
    assert labels == ["Fetch with"]
    as_user(client, GEORGE).post(f"/api/v1/sources/{sid}/versions/2/approve")
    assert source(client, sid)["fetch_engine"] == "browser"


def test_moving_a_browser_source_to_another_way_of_reading_needs_the_engine_changed_too(
    client,
) -> None:
    sid = create(client, fetch_engine="browser").json()["source"]["id"]
    as_user(client, GEORGE).post(f"/api/v1/sources/{sid}/versions/1/approve")
    url = f"/api/v1/sources/{sid}/versions"
    only_type = as_user(client, SAMARENDRA).post(url, json={"type": "RSS"})
    assert only_type.status_code == 422  # a person must choose; nothing is switched silently
    both = as_user(client, SAMARENDRA).post(url, json={"type": "RSS", "fetch_engine": "http"})
    assert both.status_code == 201, both.text


def test_an_existing_source_without_selectors_cannot_be_switched_to_the_browser(client) -> None:
    r = as_user(client, SAMARENDRA).post(
        "/api/v1/sources/trai/versions", json={"fetch_engine": "browser"}
    )  # the demo source has no selectors yet: the browser would not change that
    assert r.status_code == 422 and "needs its" in r.json()["detail"]


def test_unrelated_changes_do_not_re_check_the_reading_rules(client) -> None:
    r = as_user(client, SAMARENDRA).post(
        "/api/v1/sources/trai/versions", json={"priority": "URGENT"}
    )
    assert r.status_code == 201


def test_test_this_source_says_which_engine_was_used(client) -> None:
    sid = create(client, fetch_engine="browser").json()["source"]["id"]
    body = as_user(client, SAMARENDRA).post(f"/api/v1/sources/{sid}/test").json()
    assert body["fetch_engine"] == "browser"
    plain = as_user(client, SAMARENDRA).post("/api/v1/sources/trai/test").json()
    assert plain["fetch_engine"] == "http"


def test_source_health_marks_browser_sources(client) -> None:
    repo = get_services().repo
    rows = client.get("/api/v1/source-health/grid").json()["rows"]
    html = next(
        r
        for r in rows
        if r["channel"] == "HTML" and repo.get_source(r["source_id"]).type.value == "HTML_LIST"
    )
    assert html["fetch_engine"] == "http"
    sid = html["source_id"]
    repo.add_source_version(sid, {"fetch_engine": "browser"}, "Samarendra K.")
    version = repo.list_source_versions(sid)[-1].version
    repo.approve_source_version(sid, version, "George K.", "George K.")
    rows = client.get("/api/v1/source-health/grid").json()["rows"]
    mine = [r for r in rows if r["source_id"] == sid]
    assert {r["channel"]: r["fetch_engine"] for r in mine}["HTML"] == "browser"
    assert all(r["fetch_engine"] == "http" for r in mine if r["channel"] != "HTML")
