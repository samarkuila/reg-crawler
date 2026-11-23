from tests.api.conftest import AUDIT, DIVYA, EGAZETTE_CHECK, EOD, GEORGE, SAMARENDRA, as_user


def test_list_runs_newest_first_and_filter_by_date(client) -> None:
    runs = client.get("/api/v1/runs").json()
    assert len(runs) == 5
    assert runs[0]["id"] == "run-2026-11-20-eod"
    only = client.get("/api/v1/runs", params={"date": "2026-11-20"}).json()
    assert {r["cycle"] for r in only} == {"MORNING", "EOD"}
    assert client.get("/api/v1/runs", params={"date": "2030-01-01"}).json() == []
    assert client.get("/api/v1/runs", params={"date": "not-a-date"}).status_code == 422


def test_run_detail_has_per_source_checks(client) -> None:
    body = client.get("/api/v1/runs/run-2026-11-20-eod").json()
    assert body["run"]["status"] == "PARTIAL"
    assert body["sources_total"] == 5
    assert len(body["checks"]) == 6  # RBI RSS + RBI HTML + UNSC + TRAI + IRDAI + eGazette
    blocked = [c for c in body["checks"] if c["status"] == "BLOCKED"]
    assert [c["authority"] for c in blocked] == ["eGazette"]
    assert blocked[0]["needs_acknowledgement"] is True


def test_run_detail_404(client) -> None:
    assert client.get("/api/v1/runs/nope").status_code == 404


def test_source_health_matches_the_mockup(client) -> None:
    body = client.get("/api/v1/source-checks", params={"cycle": EOD}).json()
    assert body["headline"] == "4 of 5 sources checked"
    assert body["sources_total"] == 5 and body["sources_ok"] == 4
    assert [a["authority"] for a in body["attention"]] == ["eGazette"]
    assert body["attention"][0]["status_label"] == "Blocked"


def test_source_health_defaults_to_latest_cycle(client) -> None:
    assert client.get("/api/v1/source-checks").json()["cycle"] == EOD


def test_source_health_unknown_or_bad_cycle(client) -> None:
    assert client.get("/api/v1/source-checks", params={"cycle": "garbage"}).status_code == 422
    assert (
        client.get("/api/v1/source-checks", params={"cycle": "2030-01-01-EOD"}).status_code == 404
    )


def test_run_now_is_refused_politely_on_demo_data(client) -> None:
    r = as_user(client, DIVYA).post("/api/v1/crawl/run", json={})
    assert r.status_code == 422 and "REPO_BACKEND=sql" in r.json()["detail"]
    assert as_user(client, SAMARENDRA).post("/api/v1/crawl/run").status_code == 422


def test_crawl_run_checks_role_and_source(client) -> None:
    assert as_user(client, AUDIT).post("/api/v1/crawl/run", json={}).status_code == 403
    assert (
        as_user(client, DIVYA).post("/api/v1/crawl/run", json={"source_id": "x"}).status_code == 404
    )


def test_manual_check_acknowledges_a_blocked_source(client, audit_count) -> None:
    before = audit_count()
    r = as_user(client, DIVYA).post(
        f"/api/v1/source-checks/{EGAZETTE_CHECK}/manual-check",
        json={"note": "No relevant gazette for 20.11.2026"},
    )
    assert r.status_code == 201
    assert r.json()["user"] == "Divya G."
    assert audit_count() == before + 1
    health = client.get("/api/v1/source-checks", params={"cycle": EOD}).json()
    assert health["sources_manual"] == 1
    assert health["headline"] == "5 of 5 sources checked (1 manually)"
    assert health["attention"][0]["needs_acknowledgement"] is False
    assert health["attention"][0]["manual_check"]["note"].startswith("No relevant")


def test_manual_check_rules(client) -> None:
    url = f"/api/v1/source-checks/{EGAZETTE_CHECK}/manual-check"
    assert as_user(client, AUDIT).post(url, json={"note": "x"}).status_code == 403
    assert as_user(client, SAMARENDRA).post(url, json={"note": "x"}).status_code == 403
    assert as_user(client, DIVYA).post(url, json={"note": "  "}).status_code == 422
    assert as_user(client, DIVYA).post(url, json={}).status_code == 422
    ok_check = "chk-2026-11-20-eod-unsc-xml"
    assert (
        client.post(
            f"/api/v1/source-checks/{ok_check}/manual-check", json={"note": "x"}
        ).status_code
        == 409
    )
    assert (
        client.post("/api/v1/source-checks/nope/manual-check", json={"note": "x"}).status_code
        == 404
    )
    assert client.post(url, json={"note": "done"}).status_code == 201
    assert as_user(client, GEORGE).post(url, json={"note": "again"}).status_code == 409
