import csv
import io

from tests.api.conftest import (
    ACCEPT_IORS,
    DIVYA,
    EGAZETTE_CHECK,
    GEORGE,
    SAMARENDRA,
    as_user,
)

IORS = "item-iors-faqs"
MANUAL = "item-rbi-pdf-blocked"


def events(client, **params):
    r = client.get("/api/v1/audit", params=params)
    assert r.status_code == 200
    return r.json()


def test_seeded_history_is_listed_newest_first(client) -> None:
    rows = events(client)
    assert len(rows) >= 5
    stamps = [r["ts"] for r in rows]
    assert stamps == sorted(stamps, reverse=True)
    assert {"crawl_run.created", "source_version.requested", "review_decision.accepted"} <= {
        r["action"] for r in rows
    }


def test_every_mutation_writes_an_audit_event_with_actor_before_and_after(
    client, audit_count
) -> None:
    """One call per mutating endpoint; each must add at least one audit event by the caller."""
    mutations = [
        (DIVYA, "post", f"/api/v1/items/{IORS}/decision", {"json": ACCEPT_IORS}),
        (
            DIVYA,
            "post",
            "/api/v1/items/bulk-dismiss",
            {"json": {"item_ids": ["item-rbi-mmo"], "reason": "r"}},
        ),
        (
            DIVYA,
            "post",
            f"/api/v1/items/{MANUAL}/upload",
            {"files": {"file": ("a.pdf", b"abc", "application/pdf")}},
        ),
        (
            DIVYA,
            "post",
            f"/api/v1/source-checks/{EGAZETTE_CHECK}/manual-check",
            {"json": {"note": "n"}},
        ),
        (
            SAMARENDRA,
            "post",
            "/api/v1/sources",
            {
                "json": {
                    "id": "new-src",
                    "authority": "X",
                    "page_name": "Y",
                    "type": "RSS",
                    "url": "https://x.example",
                }
            },
        ),
        (SAMARENDRA, "post", "/api/v1/sources/trai/versions", {"json": {"priority": "URGENT"}}),
        (GEORGE, "post", "/api/v1/sources/trai/versions/2/approve", {}),
        (GEORGE, "post", "/api/v1/sources/egazette/versions/3/reject", {"json": {"reason": "r"}}),
        (SAMARENDRA, "put", "/api/v1/settings/lookback-days", {"json": {"value": 5}}),
        (GEORGE, "post", "/api/v1/settings/lookback-days/approve", {}),
        (SAMARENDRA, "put", "/api/v1/settings/holidays", {"json": {"value": ["01.01.2027"]}}),
        (GEORGE, "post", "/api/v1/settings/holidays/reject", {"json": {"reason": "r"}}),
    ]
    for user, method, url, kwargs in mutations:
        before = audit_count()
        r = getattr(as_user(client, user), method)(url, **kwargs)
        assert r.status_code < 300, (url, r.text)
        assert audit_count() > before, f"{method.upper()} {url} wrote no audit event"
        newest = events(client)[0]
        assert newest["actor"] in {"George K.", "Divya G.", "Samarendra K.", "system"}, url
        assert newest["entity"] and newest["entity_id"] and newest["action"]
    # approve / reject / state moves also carry before and after
    approved = events(client, entity="source_version")
    assert any(
        e["action"] == "source_version.approved" and e["before"] and e["after"] for e in approved
    )


def test_state_change_audit_has_before_and_after(client) -> None:
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    change = events(client, entity="regulatory_item")[0]
    assert change["action"] == "regulatory_item.state_changed"
    assert change["before"] == {"state": "IN_REVIEW"} and change["after"] == {"state": "DECIDED"}
    assert change["actor"] == "Divya G." and change["entity_id"] == IORS


def test_daily_update_audit_covers_draft_approval_and_publish(client) -> None:
    update = as_user(client, DIVYA).get("/api/v1/daily-update/preview").json()
    client.post(f"/api/v1/source-checks/{EGAZETTE_CHECK}/manual-check", json={"note": "n"})
    as_user(client, GEORGE).post(f"/api/v1/daily-update/{update['id']}/approve")
    client.get(f"/api/v1/daily-update/{update['id']}/export.eml")
    actions = [e["action"] for e in events(client, entity=update["id"])]
    assert "daily_update.approved" in actions and "daily_update.exported" in actions
    assert "daily_update.changed" in actions or "daily_update.drafted" in actions


def test_failed_mutation_leaves_no_audit_event(client, audit_count) -> None:
    before = audit_count()
    assert (
        as_user(client, DIVYA)
        .post(f"/api/v1/items/{IORS}/decision", json={"action": "dismiss"})
        .status_code
        == 422
    )
    assert client.post("/api/v1/items/nope/decision", json=ACCEPT_IORS).status_code == 404
    assert (
        as_user(client, SAMARENDRA)
        .post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
        .status_code
        == 403
    )
    assert audit_count() == before


def test_reads_do_not_write_audit_events(client, audit_count) -> None:
    client.get("/api/v1/items")
    client.get("/api/v1/sources")
    client.get("/api/v1/settings")
    n = audit_count()
    for path in (
        "/api/v1/items",
        f"/api/v1/items/{IORS}",
        "/api/v1/source-checks",
        "/api/v1/runs",
        "/api/v1/audit",
    ):
        client.get(path)
    assert audit_count() == n


def test_filters(client) -> None:
    as_user(client, DIVYA).post(f"/api/v1/items/{IORS}/decision", json=ACCEPT_IORS)
    assert {e["actor"] for e in events(client, actor="divya g.")} == {"Divya G."}
    assert all(e["entity"] == "review_decision" for e in events(client, entity="review_decision"))
    assert [e["entity_id"] for e in events(client, entity="dec-pib-2336151")] == ["dec-pib-2336151"]
    assert events(client, **{"from": "2030-01-01"}) == []
    assert events(client, to="2000-01-01") == []
    seeded = events(client, **{"from": "2026-11-20", "to": "2026-11-20"})
    assert {e["actor"] for e in seeded} >= {"scheduler", "Divya G."}
    assert client.get("/api/v1/audit", params={"from": "garbage"}).status_code == 422


def test_csv_export(client) -> None:
    r = client.get("/api/v1/audit/export.csv", params={"actor": "Divya G."})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.reader(io.StringIO(r.text)))
    assert rows[0] == ["id", "ts", "actor", "action", "entity", "entity_id", "before", "after"]
    assert len(rows) >= 3 and {row[2] for row in rows[1:]} == {"Divya G."}


def test_csv_export_neutralises_spreadsheet_formulas(client) -> None:
    as_user(client, DIVYA).post(
        f"/api/v1/source-checks/{EGAZETTE_CHECK}/manual-check", json={"note": "=HYPERLINK(1)"}
    )
    for row in csv.reader(io.StringIO(client.get("/api/v1/audit/export.csv").text)):
        assert not row[0].startswith(("=", "+", "@")) and not row[2].startswith(("=", "+", "@"))
