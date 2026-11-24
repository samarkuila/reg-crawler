"""An RSS feed or HTML listing never needs a browser: requires_js is always stored as false."""

from tests.api.conftest import GEORGE, SAMARENDRA, as_user

NEW = {
    "authority": "RBI",
    "page_name": "Notification test",
    "url": "https://example.org/notifications",
    "rss_url": "https://example.org/rss.xml",
}


def _approve(client, sid: str, version: int) -> None:
    r = as_user(client, GEORGE).post(f"/api/v1/sources/{sid}/versions/{version}/approve")
    assert r.status_code == 200, r.text


def _source(client, sid: str) -> dict:
    views = as_user(client, SAMARENDRA).get("/api/v1/sources").json()
    return next(v["source"] for v in views if v["source"]["id"] == sid)


def test_new_rss_or_html_listing_source_is_stored_without_a_browser(client):
    for kind in ("RSS", "HTML_LIST"):
        body = {**NEW, "page_name": f"Test {kind}", "type": kind, "requires_js": True}
        r = as_user(client, SAMARENDRA).post("/api/v1/sources", json=body)
        assert r.status_code == 201, r.text
        sid = r.json()["source"]["id"]
        versions = client.get(f"/api/v1/sources/{sid}/versions").json()
        assert versions[0]["after"]["requires_js"] is False
        _approve(client, sid, 1)
        assert _source(client, sid)["requires_js"] is False


def test_changing_to_rss_or_html_listing_clears_an_old_requires_js(client):
    body = {**NEW, "type": "BROWSER", "requires_js": True}
    sid = as_user(client, SAMARENDRA).post("/api/v1/sources", json=body).json()["source"]["id"]
    _approve(client, sid, 1)
    assert _source(client, sid)["requires_js"] is True  # a browser page may need one

    for kind in ("RSS", "BROWSER", "HTML_LIST"):
        # "RSS feed + HTML listing" (RSS) and "HTML listing" (HTML_LIST) must write false,
        # even though the form sent the old ticked box.
        r = as_user(client, SAMARENDRA).post(
            f"/api/v1/sources/{sid}/versions", json={"type": kind, "requires_js": True}
        )
        assert r.status_code == 201, r.text
        after = r.json()["after"]
        # Only a browser-rendered page may keep (or set) the flag.
        assert after["requires_js"] is (kind == "BROWSER")
        _approve(client, sid, r.json()["version"])
    assert _source(client, sid)["requires_js"] is False


def test_ticking_the_box_on_an_html_listing_changes_nothing(client):
    body = {**NEW, "type": "HTML_LIST", "requires_js": False}
    sid = as_user(client, SAMARENDRA).post("/api/v1/sources", json=body).json()["source"]["id"]
    _approve(client, sid, 1)
    r = as_user(client, SAMARENDRA).post(
        f"/api/v1/sources/{sid}/versions", json={"requires_js": True}
    )
    assert r.status_code == 422 and "Nothing would change" in r.json()["detail"]
