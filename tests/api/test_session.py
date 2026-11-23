import pytest

from tests.api.conftest import AUDIT, DIVYA, GEORGE, as_user

# architecture.md §5, one entry per method + path
ARCHITECTURE_ENDPOINTS = [
    ("get", "/api/v1/runs"),
    ("get", "/api/v1/runs/{run_id}"),
    ("post", "/api/v1/crawl/run"),
    ("post", "/api/v1/sources/{source_id}/test"),
    ("get", "/api/v1/source-checks"),
    ("post", "/api/v1/source-checks/{check_id}/manual-check"),
    ("get", "/api/v1/items"),
    ("get", "/api/v1/items/{item_id}"),
    ("post", "/api/v1/items/{item_id}/decision"),
    ("post", "/api/v1/items/bulk-dismiss"),
    ("post", "/api/v1/items/{item_id}/upload"),
    ("get", "/api/v1/daily-update/preview"),
    ("post", "/api/v1/daily-update/{update_id}/reorder"),
    ("post", "/api/v1/daily-update/{update_id}/approve"),
    ("get", "/api/v1/daily-update/{update_id}/export.eml"),
    ("get", "/api/v1/daily-update/{update_id}/export.html"),
    ("get", "/api/v1/sources"),
    ("post", "/api/v1/sources"),
    ("get", "/api/v1/sources/{source_id}/versions"),
    ("post", "/api/v1/sources/{source_id}/versions"),
    ("post", "/api/v1/sources/{source_id}/versions/{version}/approve"),
    ("post", "/api/v1/sources/{source_id}/versions/{version}/reject"),
    ("get", "/api/v1/settings/{key}"),
    ("put", "/api/v1/settings/{key}"),
    ("get", "/api/v1/audit"),
    ("get", "/api/v1/audit/export.csv"),
]

PAGES = [
    "/review",
    "/health",
    "/email",
    "/history",
    "/sources",
    "/settings",
    "/audit",
    "/styleguide",
]


def test_docs_lists_every_architecture_endpoint(client) -> None:
    assert client.get("/docs").status_code == 200
    paths = client.get("/openapi.json").json()["paths"]
    for method, path in ARCHITECTURE_ENDPOINTS:
        assert method in paths.get(path, {}), f"{method.upper()} {path} missing from /docs"


def test_audit_log_is_read_only_over_http(client) -> None:
    for method in ("put", "patch", "delete", "post"):
        assert getattr(client, method)("/api/v1/audit").status_code == 405


def test_me_defaults_to_reviewer_and_lists_four_users(client) -> None:
    assert client.get("/api/v1/me").json()["name"] == "Divya G."
    users = client.get("/api/v1/users").json()
    assert {u["role"] for u in users} == {"reviewer", "approver", "admin", "viewer"}
    assert {u["name"] for u in users} == {"Divya G.", "George K.", "Samarendra K.", "Audit"}


def test_switch_user_sets_cookie_and_redirects(client) -> None:
    r = client.post("/switch-user", data={"user": GEORGE, "next": "/email"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/email"
    assert client.get("/api/v1/me").json()["name"] == "George K."


@pytest.mark.parametrize("target", ["https://evil.example", "//evil.example", "/\\evil"])
def test_switch_user_never_redirects_off_site(client, target) -> None:
    r = client.post("/switch-user", data={"user": DIVYA, "next": target}, follow_redirects=False)
    assert r.headers["location"] == "/review"


def test_switch_unknown_user_is_rejected(client) -> None:
    assert client.post("/switch-user", data={"user": "nobody"}).status_code == 422


def test_unknown_cookie_falls_back_to_default_user(client) -> None:
    client.cookies.set("poc_user", "ghost")
    assert client.get("/api/v1/me").json()["id"] == DIVYA


def test_switcher_is_hidden_in_prod(client, monkeypatch) -> None:
    monkeypatch.setenv("APP_ENV", "prod")
    assert 'data-role="user-switcher"' not in client.get("/review").text
    assert client.post("/switch-user", data={"user": GEORGE}).status_code == 404


@pytest.mark.parametrize("path", PAGES)
def test_page_shells_render_with_switcher(client, path) -> None:
    r = client.get(path)
    assert r.status_code == 200
    html = r.text
    assert 'data-role="user-switcher"' in html and "POC only" in html
    assert "Divya G. · Reviewer" in html
    assert "20.11.2026 · End of day" in html  # cycle selector comes from the repository


def test_switcher_shows_selected_user(client) -> None:
    html = as_user(client, AUDIT).get("/audit").text
    assert 'value="u-audit" selected' in html


def test_root_redirects_to_review(client) -> None:
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/review"


def test_page_nav_marks_current_page(client) -> None:
    assert 'class="on" aria-current="page">Email preview' in client.get("/email").text
