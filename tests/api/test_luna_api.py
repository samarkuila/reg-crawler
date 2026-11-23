"""Luna through the API: chip and badge in the Daily Review, suggestions, Ask Luna, flags, history.
The model is a FakeLlmClient; Luna off leaves everything as it was."""

from datetime import date

import pytest

from app.agents.luna.agent import LunaAgent
from app.agents.orchestrator import AgentOrchestrator
from app.api.deps import get_services
from app.services.detection import DetectionService
from app.services.llm import FakeLlmClient, LoggedLlm
from app.services.luna import LunaService
from tests.api.conftest import AUDIT, DIVYA, SAMARENDRA, as_user
from tests.detection_helpers import crawl, make_source, row
from tests.unit.test_luna_orchestrator import SCRIPT, extract_answer, screen_for

API = "/api/v1"
D = date(2026, 12, 1)
CYCLE = "2026-12-01-ADHOC"
HIGH, MID, LOW = (
    "KYC norms for NBFC lenders",
    "A circular about reporting",
    "Auction of Government Securities",
)


@pytest.fixture
def luna(monkeypatch):
    """Luna switched on with a fake model; returns (orchestrator, fake, events)."""
    monkeypatch.setenv("AGENT_LUNA_ENABLED", "true")
    services = get_services()
    make_source(services.repo, "lun", rss_url=None)
    run = crawl(
        services.repo, "lun", D, html=[row(t, f"R/{i}", D) for i, t in enumerate((HIGH, MID, LOW))]
    )
    events = DetectionService(services.repo).process(run.id).events
    fake = FakeLlmClient(SCRIPT)
    orch = AgentOrchestrator(services.repo, lambda: LunaAgent(LoggedLlm(fake)))
    services.agent = orch
    services.luna = LunaService(services.repo, orch, services.review)
    return orch, fake, events, run


def listing(client):
    body = as_user(client, SAMARENDRA).get(f"{API}/items", params={"cycle": CYCLE}).json()
    return {i["title"]: i for i in body["items"]}, body


def test_without_luna_the_review_has_no_chips_and_works_as_before(client, monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LUNA_ENABLED", raising=False)
    services = get_services()
    make_source(services.repo, "lun", rss_url=None)
    run = crawl(services.repo, "lun", D, html=[row(HIGH, "R/0", D)])
    DetectionService(services.repo).process(run.id)
    items, body = listing(client)
    assert items[HIGH]["luna"] is None and "DISAGREE" not in items[HIGH]["badges"]
    detail = client.get(f"{API}/items/{items[HIGH]['id']}").json()
    assert detail["luna"] is None and detail["luna_enabled"] is False
    assert not any(s["present"] for s in detail["chain"] if s["kind"] == "agent")
    status = client.get(f"{API}/agent/status").json()
    assert status["enabled"] is False and status["env_enabled"] is False


def test_the_daily_review_shows_luna_chips_next_to_the_rule_rank(client, luna) -> None:
    orch, _fake, events, run = luna
    orch.process(run.id, events)
    items, _ = listing(client)
    assert items[HIGH]["luna"] == {"level": "HIGH", "score": 0.9, "disagreement": False}
    assert items[MID]["luna"]["level"] == "MEDIUM"
    assert items[LOW]["luna"]["level"] == "NONE"
    assert items[HIGH]["relevance_score"] > items[LOW]["relevance_score"]  # our rank is unchanged
    assert items[LOW]["section"] == "low_relevance"  # a NONE from Luna never hides or promotes


def test_a_disagreement_is_badged_sorted_higher_and_never_collapsed(
    client, luna, monkeypatch
) -> None:
    orch, fake, events, run = luna
    fake.script["screen"] = screen_for({HIGH: "NONE", MID: "MEDIUM", LOW: "HIGH"})
    orch.process(run.id, events)
    items, body = listing(client)
    low = items[LOW]  # rules say NONE (auction); Luna says HIGH
    assert low["luna"]["disagreement"] is True and "DISAGREE" in low["badges"]
    assert low["section"] == "attention"  # raised out of the collapsed group, never lowered
    assert items[HIGH]["luna"]["disagreement"] and items[HIGH]["section"] == "attention"
    order = [i["title"] for i in body["items"]]
    assert order.index(LOW) < order.index(MID)  # disagreements sort above plain items


def test_the_detail_shows_suggestions_with_validated_quotes(client, luna) -> None:
    orch, _fake, events, run = luna
    orch.process(run.id, events)
    item = next(e.item for e in events if e.item.title == HIGH)
    detail = as_user(client, SAMARENDRA).get(f"{API}/items/{item.id}").json()
    suggestion = detail["luna"]
    assert detail["luna_enabled"] is True
    assert (suggestion["level"], suggestion["treatment"]) == ("HIGH", "INFO_ONLY")
    assert suggestion["evidence"] == [{"quote": HIGH, "location": "title", "validated": True}]
    assert suggestion["fields"]["authority"]["value"] == "Reserve Bank of India"
    assert suggestion["fields"]["authority"]["validated"] is True
    assert (suggestion["model"], suggestion["prompt_version"]) == ("gpt-6-luna", "v1")
    assert suggestion["guidance_version"] is not None
    assert detail["latest_decision"] is None  # nothing was decided for the reviewer


def test_the_history_chain_has_an_agent_step_with_versions(client, luna) -> None:
    orch, _fake, events, run = luna
    orch.process(run.id, events)
    item = next(e.item for e in events if e.item.title == HIGH)
    trail = client.get(f"{API}/history/{item.id}/trail").json()
    step = next(s for s in trail["steps"] if s["kind"] == "agent")
    assert step["present"] and "gpt-6-luna" in step["text"] and "prompt v1" in step["text"]
    assert "guidance version" in step["text"] and "nothing was applied" in step["text"]
    assert step["title"] == "Suggested by Luna" and step["who"].startswith("agent:luna")


def test_ask_luna_fills_in_one_item_on_demand(client, luna) -> None:
    _orch, fake, events, _run = luna
    item = next(e.item for e in events if e.item.title == LOW)  # not screened yet
    r = as_user(client, DIVYA).post(f"{API}/items/{item.id}/ask-luna")
    assert r.status_code == 200
    body = r.json()
    assert body["luna"]["level"] == "NONE" and body["luna"]["fields"]["authority"]["value"]
    assert [c["task"] for c in fake.calls] == ["screen", "extract"]


def test_ask_luna_checks_role_item_and_switch(client, luna, monkeypatch) -> None:
    _orch, _fake, events, _run = luna
    item = events[0].item
    assert as_user(client, AUDIT).post(f"{API}/items/{item.id}/ask-luna").status_code == 403
    assert as_user(client, DIVYA).post(f"{API}/items/nope/ask-luna").status_code == 404
    monkeypatch.setenv("AGENT_LUNA_ENABLED", "false")
    off = as_user(client, DIVYA).post(f"{API}/items/{item.id}/ask-luna")
    assert off.status_code == 409 and "switched off" in off.json()["detail"]


def test_ask_luna_says_so_when_it_cannot_answer(client, luna) -> None:
    _orch, fake, events, _run = luna
    fake.script["screen"] = fake.script["extract"] = ConnectionError("down")
    r = as_user(client, DIVYA).post(f"{API}/items/{events[0].item.id}/ask-luna")
    assert r.status_code == 409 and "could not give a usable answer" in r.json()["detail"]


def test_possible_misses_appear_in_source_health_and_can_be_closed(client, luna) -> None:
    from tests.unit.test_luna_orchestrator import MISS, PAGE, with_snapshot

    orch, fake, events, run = luna
    repo = get_services().repo
    with_snapshot(repo, run, PAGE)
    fake.script["sweep"] = {"possible_misses": [MISS]}
    orch.process(run.id, events)
    flags = client.get(f"{API}/agent/flags").json()
    miss = next(f for f in flags if f["type"] == "POSSIBLE_MISS")
    assert miss["payload"]["title"] == "Master Direction on Digital Lending"
    assert miss["authority"] == repo.get_source("lun").authority
    grid = client.get(f"{API}/source-health/grid").json()
    row_ = next(r for r in grid["rows"] if r["source_id"] == "lun")
    assert row_["possible_misses"] == 1
    closed = as_user(client, DIVYA).post(
        f"{API}/agent/flags/{miss['id']}/resolve", json={"status": "FALSE_ALARM"}
    )
    assert closed.status_code == 200 and closed.json()["status"] == "FALSE_ALARM"
    assert closed.json()["resolved_by"] == "Divya G."
    assert client.get(f"{API}/agent/flags").json() == [] or all(
        f["type"] != "POSSIBLE_MISS" for f in client.get(f"{API}/agent/flags").json()
    )
    assert (
        as_user(client, AUDIT)
        .post(f"{API}/agent/flags/{miss['id']}/resolve", json={"status": "RESOLVED"})
        .status_code
        == 403
    )
    assert (
        as_user(client, DIVYA)
        .post(f"{API}/agent/flags/nope/resolve", json={"status": "RESOLVED"})
        .status_code
        == 404
    )


def test_agent_status_reports_the_switch_and_the_budget(client, luna) -> None:
    orch, _fake, events, run = luna
    orch.process(run.id, events)
    status = client.get(f"{API}/agent/status").json()
    assert status["enabled"] is True and status["setting_enabled"] is True
    assert status["daily_budget_inr"] == 200 and status["spent_today_inr"] > 0
    assert extract_answer is not None
