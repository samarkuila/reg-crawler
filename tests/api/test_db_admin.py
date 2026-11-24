"""Admin Database page: role, locked tables, audited edits and deletes, reset (SQL backend only)."""

import pytest

from app.repositories import seed
from tests.api.conftest import AUDIT, DIVYA, SAMARENDRA, as_user


@pytest.fixture
def admin(client, fresh_repo):
    if fresh_repo != "sql":
        pytest.skip("The Database page needs the SQL backend")
    return as_user(client, SAMARENDRA)


def test_only_an_admin_can_open_it(client, fresh_repo):
    if fresh_repo != "sql":
        pytest.skip("SQL only")
    for user in (DIVYA, AUDIT):
        assert as_user(client, user).get("/api/v1/admin/db/tables").status_code == 403
    assert as_user(client, SAMARENDRA).get("/api/v1/admin/db/tables").status_code == 200


def test_memory_backend_says_what_to_do(client, fresh_repo):
    if fresh_repo != "memory":
        pytest.skip("memory only")
    r = as_user(client, SAMARENDRA).get("/api/v1/admin/db/tables")
    assert r.status_code == 409 and "REPO_BACKEND=sql" in r.json()["detail"]


def test_write_once_tables_are_browse_only(admin):
    tables = {t["name"]: t for t in admin.get("/api/v1/admin/db/tables").json()}
    for name in ("snapshot", "document", "audit_event", "ai_extraction", "alembic_version"):
        assert tables[name]["editable"] is False
    assert tables["source"]["editable"] is True
    row = admin.get("/api/v1/admin/db/tables/audit_event?limit=1").json()["rows"][0]
    r = admin.post(
        "/api/v1/admin/db/tables/audit_event/rows/delete", json={"key": {"id": row["id"]}}
    )
    assert r.status_code == 409


def test_edit_and_delete_are_audited(admin, audit_count):
    page = admin.get("/api/v1/admin/db/tables/app_user").json()
    assert page["editable"] and any(c["primary_key"] for c in page["columns"])
    before = audit_count()
    r = admin.put(
        "/api/v1/admin/db/tables/app_user/rows",
        json={"key": {"id": "u-audit"}, "changes": {"name": "Audit team"}},
    )
    assert r.status_code == 200 and r.json()["name"] == "Audit team"
    assert audit_count() == before + 1
    event = admin.get("/api/v1/audit/view", params={"action": "admin.row_updated"}).json()["rows"][
        0
    ]
    assert event["actor"] == "Samarendra K." and event["entity"] == "app_user"

    r = admin.post("/api/v1/admin/db/tables/app_user/rows/delete", json={"key": {"id": "u-audit"}})
    assert r.status_code == 200 and r.json() == {"deleted": 1}
    assert audit_count() == before + 2
    assert all(
        row["id"] != "u-audit"
        for row in admin.get("/api/v1/admin/db/tables/app_user").json()["rows"]
    )


def test_bad_edits_are_refused(admin):
    url = "/api/v1/admin/db/tables/app_user/rows"
    assert (
        admin.put(url, json={"key": {"id": "u-audit"}, "changes": {"nope": "x"}}).status_code == 422
    )
    assert (
        admin.put(url, json={"key": {"id": "u-audit"}, "changes": {"id": "other"}}).status_code
        == 422
    )
    assert (
        admin.put(url, json={"key": {"id": "missing"}, "changes": {"name": "x"}}).status_code == 404
    )
    assert admin.get("/api/v1/admin/db/tables/not_a_table").status_code == 404


def test_reset_needs_the_word_and_keeps_users(admin, tmp_path):
    assert admin.post("/api/v1/admin/db/reset", json={"confirm": "yes"}).status_code == 422
    r = admin.post("/api/v1/admin/db/reset", json={"confirm": "RESET"})
    assert r.status_code == 200, r.text
    tables = {t["name"]: t["rows"] for t in admin.get("/api/v1/admin/db/tables").json()}
    assert tables["source"] == 0 and tables["regulatory_item"] == 0
    assert tables["app_user"] == len(seed.USERS)
    assert tables["audit_event"] == 1  # the reset itself is recorded


def test_a_blocked_delete_names_the_table(admin):
    sid = admin.get("/api/v1/admin/db/tables/source_check?limit=1").json()["rows"][0]["source_id"]
    r = admin.post("/api/v1/admin/db/tables/source/rows/delete", json={"key": {"id": sid}})
    assert r.status_code == 409
    assert "in source_check" in r.json()["detail"]


def test_cascade_deletes_the_rows_that_point_to_it(admin, audit_count):
    sid = admin.get("/api/v1/admin/db/tables/source_version?limit=1").json()["rows"][0]["source_id"]
    url = "/api/v1/admin/db/tables/source/rows/delete"
    assert admin.post(url, json={"key": {"id": sid}}).status_code == 409  # refused by default
    before = audit_count()
    r = admin.post(url, json={"key": {"id": sid}, "cascade": True})
    if r.status_code == 409:  # this seed source also has write-once history: it must stay blocked
        assert "cannot be changed here" in r.json()["detail"]
        return
    assert r.status_code == 200 and r.json()["deleted"] >= 2
    assert audit_count() == before + r.json()["deleted"]  # one audit event per row removed
    ids = [row["id"] for row in admin.get("/api/v1/admin/db/tables/source").json()["rows"]]
    assert sid not in ids


def test_a_row_can_be_added_and_is_audited(admin, audit_count):
    url = "/api/v1/admin/db/tables/app_user/rows"
    page = admin.get("/api/v1/admin/db/tables/app_user").json()
    role = next(c for c in page["columns"] if c["name"] == "role")
    assert "reviewer" in role["choices"]  # the page can offer a dropdown
    before = audit_count()
    new = {"id": "u-new", "name": "New Person", "email": "new@example.invalid", "role": "reviewer"}
    r = admin.post(url, json={"values": new})
    assert r.status_code == 201 and r.json() == new
    assert audit_count() == before + 1
    event = admin.get("/api/v1/audit/view", params={"action": "admin.row_created"}).json()["rows"][
        0
    ]
    assert event["entity"] == "app_user" and event["actor"] == "Samarendra K."
    assert any(
        row["id"] == "u-new" for row in admin.get("/api/v1/admin/db/tables/app_user").json()["rows"]
    )


def test_a_bad_new_row_is_refused(admin):
    url = "/api/v1/admin/db/tables/app_user/rows"
    ok = {"id": "u-x", "name": "X", "email": "x@example.invalid", "role": "viewer"}
    assert admin.post(url, json={"values": {**ok, "id": ""}}).status_code == 422  # key missing
    assert admin.post(url, json={"values": {**ok, "role": "boss"}}).status_code == 409  # not a role
    assert admin.post(url, json={"values": {**ok, "nope": "1"}}).status_code == 422
    assert admin.post(url, json={"values": {**ok, "id": "u-audit"}}).status_code == 409  # taken
    assert (
        admin.post("/api/v1/admin/db/tables/audit_event/rows", json={"values": ok}).status_code
        == 409
    )
