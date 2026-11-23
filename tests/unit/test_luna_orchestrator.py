"""Orchestrator: queueing, concurrency, budget, disagreement, sweep, and 'Luna never decides'."""

import re
import sqlite3
import threading
import time
from datetime import date

import pytest

from app.agents.luna.agent import LunaAgent
from app.agents.orchestrator import AgentOrchestrator
from app.schemas.api import ItemFilter
from app.schemas.entities import Snapshot, now_ist
from app.schemas.enums import (
    AgentStatus,
    AgentTask,
    AlertKind,
    CheckChannel,
    FlagStatus,
    FlagType,
    ItemState,
)
from app.services.detection import DetectionService
from app.services.llm import FakeLlmClient, LoggedLlm
from tests.conftest import BACKENDS, make_backend
from tests.detection_helpers import crawl, make_source, row

D1 = date(2026, 12, 1)
HIGH_TITLE = "KYC norms for NBFC lenders"  # rule score 88: HIGH
MID_TITLE = "A circular about reporting"  # no keyword: 50, MEDIUM
LOW_TITLE = "Auction of Government Securities"  # excluded word: 20, NONE


@pytest.fixture(params=BACKENDS)
def env(request, tmp_path, sql_template):
    repo, url, files = make_backend(request.param, tmp_path, sql_template)
    make_source(repo, "lun", rss_url=None)
    return repo, url, request.param


def title_of(user: str) -> str:
    return re.search(r"- Title: (.+)", user).group(1).strip()


def screen_for(levels: dict[str, str], **over):
    """A screen answer whose relevance depends on the item title; its quote is the title."""

    def answer(system: str, user: str):
        title = title_of(user)
        level = levels.get(title, "MEDIUM")
        return {
            "nbfc_relevance": level,
            "relevance_score": {"HIGH": 0.9, "MEDIUM": 0.6, "LOW": 0.3, "NONE": 0.05}[level],
            "addressed_to": ["NBFCs"],
            "reason": f"Because of {title}.",
            "suggested_treatment": "INFO_ONLY",
            "suggested_sme_function": "None",
            "urgency": "NORMAL",
            "evidence": [{"quote": title, "location": "title"}],
            **over,
        }

    return answer


def extract_answer(system: str, user: str):
    title = title_of(user)
    null = {"value": None, "evidence_quote": None, "location": None, "confidence": None}
    fields = {
        n: dict(null)
        for n in (
            "ref_no", "issue_date", "publish_date", "effective_date", "effective_date_rule",
            "authority", "document_type", "impact_summary",
        )
    }  # fmt: skip
    fields["authority"] = {"value": "Reserve Bank of India", "evidence_quote": title,
                           "location": "title", "confidence": 0.8}  # fmt: skip
    return fields


def events_for(repo, *titles):
    run = crawl(repo, "lun", D1, html=[row(t, f"R/{i}", D1) for i, t in enumerate(titles)])
    return run, DetectionService(repo).process(run.id).events


def orchestrator(repo, script, *, enabled=True, **kw):
    fake = FakeLlmClient(script, cost_inr=kw.pop("cost", 0.5))
    return AgentOrchestrator(
        repo, lambda: LunaAgent(LoggedLlm(fake)), enabled=lambda: enabled, **kw
    ), fake


SCRIPT = {
    "screen": screen_for({HIGH_TITLE: "HIGH", MID_TITLE: "MEDIUM", LOW_TITLE: "NONE"}),
    "extract": extract_answer,
}


# --- the normal path -----------------------------------------------------------------


def test_new_items_are_screened_and_saved_as_suggestions(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE, LOW_TITLE)
    before = {e.item.id: e.item.relevance_score for e in events}
    orch, fake = orchestrator(repo, SCRIPT)
    summary = orch.process(run.id, events)
    assert summary.enabled and summary.screened == 2
    for event in events:
        (screen,) = [x for x in repo.list_extractions(event.item.id) if x.task == AgentTask.SCREEN]
        assert screen.prompt_version == "v1" and screen.model == "gpt-6-luna"
        assert screen.fields["evidence"][0]["validated"] is True
        item = repo.get_item(event.item.id)
        assert item.state == ItemState.ENRICHED  # at most ENRICHED
        assert item.relevance_score == before[item.id]  # Luna never changes our score
    levels = {
        e.item.title: repo.list_extractions(e.item.id)[0].nbfc_relevance.value for e in events
    }
    assert levels == {
        HIGH_TITLE: "HIGH",
        LOW_TITLE: "NONE",
    }  # the NONE item still exists, collapsed
    assert len(fake.calls) >= 2


def test_every_call_is_logged_with_model_versions_tokens_and_cost(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    orchestrator(repo, SCRIPT)[0].process(run.id, events)
    runs = repo.list_agent_runs()
    assert runs and all(r.status in (AgentStatus.OK, AgentStatus.QUOTE_REJECTED) for r in runs)
    first = next(r for r in runs if r.task == AgentTask.SCREEN)
    assert (first.model, first.deployment, first.prompt_version) == (
        "gpt-6-luna",
        "fake-deployment",
        "v1",
    )
    assert first.guidance_version is not None  # the version of the screening guidance used
    assert (first.tokens_in, first.tokens_out, first.cost_inr) == (800, 150, 0.5)
    assert first.run_id == run.id and first.item_id == events[0].item.id


def test_extract_runs_only_for_medium_and_high(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE, MID_TITLE, LOW_TITLE)
    summary = orchestrator(repo, SCRIPT)[0].process(run.id, events)
    assert summary.extracted == 2
    extracted = {
        e.item.title
        for e in events
        if any(x.task == AgentTask.EXTRACT for x in repo.list_extractions(e.item.id))
    }
    assert extracted == {HIGH_TITLE, MID_TITLE}


def test_only_new_modified_and_backdated_items_are_screened(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    orch, fake = orchestrator(repo, SCRIPT)
    orch.process(run.id, events)
    again = crawl(repo, "lun", date(2026, 12, 2), html=[row(HIGH_TITLE, "R/0", D1)])
    result = DetectionService(repo).process(again.id)
    assert result.events == []  # unchanged: nothing to screen
    calls_before = len(fake.calls)
    assert orch.process(again.id, result.events).screened == 0 and len(fake.calls) == calls_before


def test_the_screening_guidance_and_past_decisions_are_in_the_prompt(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    orch, fake = orchestrator(repo, SCRIPT)
    orch.process(run.id, events)
    user = fake.calls[0]["user"]
    assert "A regulatory update is anything where a regulator requires" in user  # guidance text
    assert "(version" in user and "<untrusted_document>" in user
    assert "->" in user  # examples drawn from approved past decisions in the demo data


# --- off, and nothing to decide ------------------------------------------------------


def test_when_luna_is_off_nothing_is_called_and_items_are_untouched(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    orch, fake = orchestrator(repo, SCRIPT, enabled=False)
    summary = orch.process(run.id, events)
    assert not summary.enabled and fake.calls == []
    assert repo.list_extractions(events[0].item.id) == [] and repo.list_agent_runs() == []
    assert repo.get_item(events[0].item.id).state == ItemState.DETECTED


def test_a_fooled_model_cannot_decide_hide_or_send(env) -> None:
    repo, _url, _backend = env
    decisions_before = len(repo.list_decisions(next(iter(repo.list_items(ItemFilter()))).id))
    run, events = events_for(repo, HIGH_TITLE)
    evil = {"screen": lambda s, u: {**SCRIPT["screen"](s, u), "decision": "ACCEPT", "send": True}}
    orch, _ = orchestrator(repo, evil)
    summary = orch.process(run.id, events)
    item = repo.get_item(events[0].item.id)
    assert summary.screened == 0 and item.state == ItemState.DETECTED  # the answer was thrown away
    assert repo.list_decisions(item.id) == [] and decisions_before >= 0
    assert repo.list_extractions(item.id) == []


def test_fabricated_quotes_leave_no_suggestions_but_the_item_flows(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    fake_quote = {
        "screen": screen_for({}, evidence=[{"quote": "Never said anywhere", "location": "p1"}])
    }
    orch, _ = orchestrator(repo, fake_quote)
    summary = orch.process(run.id, events)
    assert summary.screened == 0 and repo.list_extractions(events[0].item.id) == []
    assert repo.get_item(events[0].item.id) is not None
    assert repo.list_agent_runs()[0].status == AgentStatus.QUOTE_REJECTED  # logged for review


# --- concurrency, budget, outages ----------------------------------------------------


def test_at_most_four_calls_run_at_once(env) -> None:
    repo, _url, _backend = env
    titles = [f"KYC notice number {n}" for n in range(12)]
    run, events = events_for(repo, *titles)
    live, peak, lock = 0, 0, threading.Lock()

    def slow(system: str, user: str):
        nonlocal live, peak
        with lock:
            live += 1
            peak = max(peak, live)
        time.sleep(0.05)
        with lock:
            live -= 1
        return screen_for({})(system, user)

    orch, _ = orchestrator(repo, {"screen": slow, "extract": extract_answer})
    orch.process(run.id, events)
    assert 2 <= peak <= 4  # parallel, but never more than four


def test_the_daily_budget_stops_luna_and_items_continue(env) -> None:
    repo, _url, _backend = env
    repo.propose_setting("agent-daily-budget-inr", 1.0, "George K.", "George K.")
    repo.approve_setting("agent-daily-budget-inr", "Divya G.", "Divya G.")
    titles = [f"KYC notice number {n}" for n in range(8)]
    run, events = events_for(repo, *titles)
    orch, fake = orchestrator(repo, SCRIPT, cost=0.6, max_workers=1)
    summary = orch.process(run.id, events)
    assert summary.stopped_because == "budget" and summary.screened == 2  # 0.6 + 0.6 >= 1.0
    assert len(fake.calls) == 2  # nothing is sent after the cap
    statuses = [r.status for r in repo.list_agent_runs()]
    assert AgentStatus.BUDGET in statuses
    assert all(repo.get_item(e.item.id) is not None for e in events)  # every item still there
    alerts = repo.list_alerts(run.id)
    assert any(a.kind == AlertKind.RUN_PROBLEM and "budget" in a.message for a in alerts)


def test_the_budget_counts_what_was_already_spent_today(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    repo.propose_setting("agent-daily-budget-inr", 1.0, "George K.", "George K.")
    repo.approve_setting("agent-daily-budget-inr", "Divya G.", "Divya G.")
    orchestrator(repo, SCRIPT, cost=2.0)[0].process(run.id, events)  # spends more than the cap
    run2, events2 = events_for(repo, "KYC second notice")
    orch, fake = orchestrator(repo, SCRIPT)
    assert orch.process(run2.id, events2).stopped_because == "budget" and fake.calls == []


def test_repeated_provider_errors_mean_luna_unavailable_and_no_crash(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, *[f"KYC notice number {n}" for n in range(8)])
    orch, fake = orchestrator(repo, {"screen": ConnectionError("down")}, max_workers=1)
    summary = orch.process(run.id, events)
    assert summary.stopped_because == "unavailable" and summary.screened == 0
    assert len(fake.calls) == 3  # it gives up after three failures in a row
    assert any("Luna unavailable" in a.message for a in repo.list_alerts(run.id))
    assert all(repo.get_item(e.item.id).state == ItemState.DETECTED for e in events)


# --- disagreement --------------------------------------------------------------------


def test_rules_and_luna_two_levels_apart_raise_a_flag(env) -> None:
    repo, _url, _backend = env
    answers = {HIGH_TITLE: "NONE", MID_TITLE: "NONE", LOW_TITLE: "HIGH"}
    run, events = events_for(repo, HIGH_TITLE, MID_TITLE, LOW_TITLE)
    summary = orchestrator(repo, {"screen": screen_for(answers), "extract": extract_answer})[
        0
    ].process(run.id, events)
    flagged = {
        e.item.title: repo.list_agent_flags(item_id=e.item.id, status=FlagStatus.OPEN)
        for e in events
    }
    assert summary.disagreements == 3
    assert all(len(f) == 1 and f[0].type == FlagType.DISAGREEMENT for f in flagged.values())
    payload = flagged[LOW_TITLE][0].payload
    assert (payload["rule_level"], payload["luna_level"]) == ("NONE", "HIGH")


def test_one_level_apart_is_not_a_disagreement(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    orch, _ = orchestrator(
        repo, {"screen": screen_for({HIGH_TITLE: "MEDIUM"}), "extract": extract_answer}
    )
    assert orch.process(run.id, events).disagreements == 0


def test_a_flag_is_not_repeated_for_the_same_item(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    orch, _ = orchestrator(
        repo, {"screen": screen_for({HIGH_TITLE: "NONE"}), "extract": extract_answer}
    )
    orch.process(run.id, events)
    orch.process(run.id, events)
    assert len(repo.list_agent_flags(item_id=events[0].item.id)) == 1


# --- sweep ---------------------------------------------------------------------------


def with_snapshot(repo, run, html: str):
    import hashlib

    body = html.encode()
    digest = hashlib.sha256(body).hexdigest()
    snap = repo.record_snapshot(
        Snapshot(
            id="snap-sweep", source_id="lun", channel=CheckChannel.HTML, url="https://x/list",
            fetched_at=now_ist(), sha256=digest, path=f"data/snapshots/lun/2026-12-01/{digest}",
        ),
        body,
        "t",
    )  # fmt: skip
    check = next(c for c in repo.list_source_checks(run.id) if c.source_id == "lun")
    repo.record_source_check(check.model_copy(update={"snapshot_id": snap.id}), "t")


PAGE = (
    "<html><body><ul><li>KYC norms for NBFC lenders</li>"
    "<li>Master Direction on Digital Lending, 20 Nov 2026</li>"
    "<li>Tender for chairs</li></ul></body></html>"
)
MISS = {
    "title": "Master Direction on Digital Lending", "date_text": "20 Nov 2026", "link_text": "",
    "why_regulatory": "A direction.",
    "evidence_quote": "Master Direction on Digital Lending, 20 Nov 2026",
}  # fmt: skip


def test_the_sweep_flags_a_possible_miss_once_per_snapshot(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    with_snapshot(repo, run, PAGE)
    orch, fake = orchestrator(repo, {**SCRIPT, "sweep": {"possible_misses": [MISS]}})
    summary = orch.process(run.id, events)
    assert summary.swept == 1 and summary.possible_misses == 1
    (flag,) = repo.list_agent_flags(source_id="lun", status=FlagStatus.OPEN)
    assert flag.type == FlagType.POSSIBLE_MISS and flag.snapshot_id == "snap-sweep"
    assert (
        flag.payload["title"] == "Master Direction on Digital Lending" and flag.payload["validated"]
    )
    sweeps = [c for c in fake.calls if c["task"] == "sweep"]
    assert "KYC norms for NBFC lenders" in sweeps[0]["user"]  # the parsed items are listed
    orch.process(run.id, events)  # the same snapshot is not swept again
    assert len([c for c in fake.calls if c["task"] == "sweep"]) == 1
    assert len(repo.list_agent_flags(source_id="lun")) == 1


def test_a_fabricated_miss_never_becomes_a_flag(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    with_snapshot(repo, run, PAGE)
    invented = {
        **MISS,
        "title": "Circular on crypto",
        "evidence_quote": "Circular on crypto, 21 Nov",
    }
    orchestrator(repo, {**SCRIPT, "sweep": {"possible_misses": [invented]}})[0].process(
        run.id, events
    )
    assert repo.list_agent_flags(source_id="lun") == []


def test_a_flag_can_be_resolved_by_a_person(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, HIGH_TITLE)
    with_snapshot(repo, run, PAGE)
    orchestrator(repo, {**SCRIPT, "sweep": {"possible_misses": [MISS]}})[0].process(run.id, events)
    (flag,) = repo.list_agent_flags(source_id="lun")
    done = repo.resolve_agent_flag(flag.id, FlagStatus.FALSE_ALARM, "Divya G.", "Divya G.")
    assert (done.status, done.resolved_by) == (
        FlagStatus.FALSE_ALARM,
        "Divya G.",
    ) and done.resolved_at
    assert repo.list_agent_flags(source_id="lun", status=FlagStatus.OPEN) == []


# --- ask Luna ------------------------------------------------------------------------


def test_ask_luna_screens_and_extracts_one_item_whatever_its_relevance(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, LOW_TITLE)
    orch, fake = orchestrator(repo, SCRIPT)
    summary = orch.ask(events[0].item.id)
    assert summary.screened == 1 and summary.extracted == 1
    tasks = {x.task for x in repo.list_extractions(events[0].item.id)}
    assert tasks == {AgentTask.SCREEN, AgentTask.EXTRACT}
    assert [c["task"] for c in fake.calls] == ["screen", "extract"]


def test_ask_luna_does_nothing_when_off(env) -> None:
    repo, _url, _backend = env
    run, events = events_for(repo, LOW_TITLE)
    orch, fake = orchestrator(repo, SCRIPT, enabled=False)
    assert not orch.ask(events[0].item.id).enabled and fake.calls == []


# --- evidence is immutable -----------------------------------------------------------


def test_a_stored_suggestion_cannot_be_changed_or_deleted(env) -> None:
    repo, url, backend = env
    if backend != "sql":
        pytest.skip("the database trigger exists only on the SQL backend")
    run, events = events_for(repo, HIGH_TITLE)
    orchestrator(repo, SCRIPT)[0].process(run.id, events)
    (screen,) = [x for x in repo.list_extractions(events[0].item.id) if x.task == AgentTask.SCREEN]
    con = sqlite3.connect(url.removeprefix("sqlite:///"))
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("UPDATE ai_extraction SET nbfc_relevance='NONE' WHERE id=?", (screen.id,))
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("DELETE FROM ai_extraction WHERE id=?", (screen.id,))
    finally:
        con.close()
