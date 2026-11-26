"""Run now through the API: a full run (fake crawler), progress, source health and review update,
a second run refused politely, alerts. The crawler is a fake; the real one has its own test."""

import pytest

from app.api.deps import get_services
from app.services.crawl_runner import CrawlRunner, RunLock
from tests.api.conftest import AUDIT, DIVYA, GEORGE, SAMARENDRA, as_user
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


def test_first_crawl_gets_the_window_then_the_lookback_setting(client, launcher) -> None:
    body = {
        "authority": "NewReg",
        "page_name": "Fresh page",
        "type": "HTML_LIST",
        "url": "https://newreg.example.gov.in/list",
        "adapter_config": {
            "selectors": {"row": "tr", "title": "a", "link": "a"},
            "date_format": "%d.%m.%Y",
        },
    }
    made = as_user(client, SAMARENDRA).post(f"{API}/sources", json=body).json()
    sid = made["source"]["id"]
    as_user(client, GEORGE).post(f"{API}/sources/{sid}/versions/1/approve")

    as_user(client, DIVYA).post(f"{API}/crawl/run", json={})
    wait()
    first = dict(zip(launcher.calls, launcher.args, strict=True))
    mine = [a for (src, _), a in first.items() if src == sid]
    assert mine and all(a["first_run"] and a["window"] for a in mine)  # never crawled: the window
    assert all(a["max_requests"] <= 50 for a in first.values())
    others = [a for (src, _), a in first.items() if src != sid]
    assert others and not any(a["first_run"] for a in others)  # demo sources are past their first
    assert set(launcher.lookbacks) == {None, 2}


def test_crawl_status_shows_a_running_crawl_then_goes_quiet(client, launcher, tmp_path) -> None:
    assert client.get(f"{API}/crawl/status").json()["running"] is False
    seen: list[dict] = []
    launcher.before = lambda *_a: seen.append(client.get(f"{API}/crawl/status").json())
    as_user(client, DIVYA).post(f"{API}/crawl/run", json={})
    wait()
    assert seen and all(s["running"] for s in seen)
    assert seen[0]["phase"] == "reading" and seen[0]["current"]
    assert seen[0]["sources_total"] > 1 and seen[-1]["sources_done"] >= seen[0]["sources_done"]
    assert seen[0]["run"]["status"] == "RUNNING"
    after = client.get(f"{API}/crawl/status").json()
    assert after["running"] is False and after["run"] is None


def test_fifty_requests_per_website_per_run_is_never_exceeded(client, launcher) -> None:
    from app.schemas.entities import SourceCheck
    from app.schemas.enums import CheckChannel, CheckStatus

    ids = []
    for n in (1, 2):
        body = {
            "authority": "CapReg",
            "page_name": f"Page {n}",
            "type": "HTML_LIST",
            "url": f"https://capreg.example.gov.in/list{n}",
            "adapter_config": {
                "selectors": {"row": "tr", "title": "a", "link": "a"},
                "date_format": "%d.%m.%Y",
            },
        }
        sid = as_user(client, SAMARENDRA).post(f"{API}/sources", json=body).json()["source"]["id"]
        as_user(client, GEORGE).post(f"{API}/sources/{sid}/versions/1/approve")
        ids.append(sid)

    def spend_fifty(repo, spider, source_id, run_id):
        repo.record_source_check(
            SourceCheck(
                id=f"chk-{run_id}-{source_id}-html",
                run_id=run_id,
                source_id=source_id,
                channel=CheckChannel.HTML,
                status=CheckStatus.OK,
                items_found=1,
                stats={"requests_made": 50},
            ),
            "crawler",
        )

    launcher.script[(ids[0], "listing")] = spend_fifty
    run = as_user(client, DIVYA).post(f"{API}/crawl/run", json={}).json()
    wait()
    checks = {c["source_id"]: c for c in client.get(f"{API}/runs/{run['id']}").json()["checks"]}
    assert checks[ids[0]]["status"] == "OK" and checks[ids[0]]["stats"]["requests_made"] == 50
    assert checks[ids[1]]["status"] == "FAILED"  # not silently skipped: the check says why
    assert "limit of 50 page requests" in checks[ids[1]]["error_detail"]
    assert ids[1] not in {s for (s, _sp) in launcher.calls}  # no request was made for it
