"""Run now through the API: a full run (fake crawler), progress, source health and review update,
a second run refused politely, alerts. The crawler is a fake; the real one has its own test."""

import pytest

from app.api.deps import get_services
from app.services.crawl_runner import CrawlRunner, RunLock
from tests.api.conftest import AUDIT, DIVYA, GEORGE, as_user
from tests.crawl_helpers import FakeLauncher, Gate

API = "/api/v1"


@pytest.fixture
def launcher(tmp_path):
    services = get_services()
    fake = FakeLauncher(services.repo)
    services.crawl = CrawlRunner(services.repo, fake, lock=RunLock(tmp_path / "crawl.lock"))
    return fake


def wait() -> None:
    get_services().crawl.wait(30)


def test_run_now_completes_and_source_health_and_review_update(client, launcher) -> None:
    started = as_user(client, DIVYA).post(f"{API}/crawl/run", json={})
    assert started.status_code == 202
    run = started.json()
    assert (
        run["status"] == "RUNNING" and run["triggered_by"] == "Divya G." and run["cycle"] == "ADHOC"
    )
    wait()
    detail = client.get(f"{API}/runs/{run['id']}").json()
    assert detail["run"]["status"] == "COMPLETED" and detail["run"]["ended_at"]
    assert detail["sources_done"] == detail["sources_total"] > 1
    assert {c["status"] for c in detail["checks"]} == {"OK"}
    cycle = f"{run['cycle_date']}-ADHOC"
    health = client.get(f"{API}/source-checks", params={"cycle": cycle}).json()
    assert health["run_id"] == run["id"] and health["sources_ok"] == detail["sources_total"]
    items = client.get(f"{API}/items", params={"cycle": cycle}).json()
    assert (
        items["counts"]["new"] >= 1 and items["counts"]["total"] >= 1
    )  # the Daily review has them


def test_the_run_is_listed_with_who_started_it(client, launcher) -> None:
    run = as_user(client, GEORGE).post(f"{API}/crawl/run", json={"source_id": "unsc"}).json()
    wait()
    listed = client.get(f"{API}/runs", params={"date": run["cycle_date"]}).json()
    mine = next(r for r in listed if r["id"] == run["id"])
    assert mine["triggered_by"] == "George K." and mine["sources"] == ["unsc"]
    detail = client.get(f"{API}/runs/{run['id']}").json()
    assert (detail["sources_total"], detail["sources_done"]) == (1, 1)


def test_a_second_run_now_while_running_is_refused_politely(client, tmp_path) -> None:
    services = get_services()
    gate = Gate()
    services.crawl = CrawlRunner(
        services.repo,
        FakeLauncher(services.repo, before=gate),
        lock=RunLock(tmp_path / "crawl.lock"),
    )
    first = as_user(client, DIVYA).post(f"{API}/crawl/run", json={"source_id": "unsc"})
    assert first.status_code == 202 and gate.entered.wait(10)
    second = as_user(client, GEORGE).post(f"{API}/crawl/run", json={})
    assert second.status_code == 409
    message = second.json()["detail"]
    assert "already running" in message and "Divya G." in message and "Source health" in message
    gate.release.set()
    wait()
    assert client.get(f"{API}/runs/{first.json()['id']}").json()["run"]["status"] == "COMPLETED"
    assert (
        as_user(client, GEORGE).post(f"{API}/crawl/run", json={"source_id": "unsc"}).status_code
        == 202
    )
    wait()


def test_run_now_checks_role_and_source(client, launcher) -> None:
    assert as_user(client, AUDIT).post(f"{API}/crawl/run", json={}).status_code == 403
    assert (
        as_user(client, DIVYA).post(f"{API}/crawl/run", json={"source_id": "nope"}).status_code
        == 404
    )
    planned = next(s for s in get_services().repo.list_sources() if s.planned)
    refused = as_user(client, DIVYA).post(f"{API}/crawl/run", json={"source_id": planned.id})
    assert refused.status_code == 422 and "not active" in refused.json()["detail"]
    assert launcher.calls == []  # nothing was started


def test_alerts_for_problem_sources_and_urgent_items(client, launcher) -> None:
    from app.schemas.enums import CheckStatus
    from tests.crawl_helpers import write_status

    launcher.script[("trai", "listing")] = lambda r, sp, src, run_id: write_status(
        r, sp, src, run_id, CheckStatus.BLOCKED, http_status=403, error_detail="HTTP 403"
    )
    run = as_user(client, DIVYA).post(f"{API}/crawl/run", json={}).json()
    wait()
    alerts = client.get(f"{API}/alerts").json()  # defaults to the latest run
    assert alerts and {a["run_id"] for a in alerts} == {run["id"]}
    assert alerts[0]["severity"] == "urgent"  # urgent first
    kinds = {a["kind"] for a in alerts}
    assert {"SOURCE_BLOCKED", "URGENT_ITEM"} <= kinds
    blocked = next(a for a in alerts if a["kind"] == "SOURCE_BLOCKED")
    assert "blocked the crawler" in blocked["message"] and blocked["delivered_at"] is None
    assert client.get(f"{API}/alerts", params={"run_id": run["id"]}).json() == alerts
    assert client.get(f"{API}/runs/{run['id']}").json()["run"]["status"] == "PARTIAL"


def test_alerts_are_empty_before_any_run(client) -> None:
    get_services().repo.list_runs()  # the demo data has runs but no alerts
    assert client.get(f"{API}/alerts").json() == []
