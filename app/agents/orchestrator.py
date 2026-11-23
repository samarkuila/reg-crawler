"""Runs Luna after detection and documents (spec §5, §10; system design §3.1 step 7).

    screen   every NEW / MODIFIED / BACKDATED item
    extract  items Luna rates MEDIUM or HIGH (and on demand: "Ask Luna")
    sweep    each listing snapshot once, to look for items the parser missed

Rules enforced here (not only in prompts):
- Advisory only: results go to ai_extraction, agent_flag and agent_run. Nothing here sets a
  decision, hides or removes an item, or changes its relevance score. An item moves to ENRICHED at
  most.
- At most 4 calls at once. Workers only call the model and check the answer; the main thread does
  all the writing.
- A daily budget (INR). When it is used up, or the provider fails repeatedly, the remaining items
  are simply left without suggestions and an alert says "Luna unavailable". Items continue.
- Rules and Luna disagree by two levels or more -> a DISAGREEMENT flag; the review page sorts that
  item higher, never lower.
"""

import logging
import threading
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any

from app.agents import settings
from app.agents.luna.agent import LunaAgent, Screened
from app.repositories.base import Repository
from app.schemas.entities import AgentFlag, AiExtraction, Alert, RegulatoryItem, now_ist
from app.schemas.enums import (
    AgentStatus,
    AgentTask,
    AlertKind,
    ChangeType,
    FlagStatus,
    FlagType,
    ItemState,
    NbfcRelevance,
    SourceType,
)
from app.services.llm import Budget, todays_spend
from app.services.normalise import html_to_text

log = logging.getLogger(__name__)
ACTOR = "agent:luna"
MAX_WORKERS = 4
MAX_CONSECUTIVE_ERRORS = 3
SCREENED = (ChangeType.NEW, ChangeType.MODIFIED, ChangeType.BACKDATED)
EXTRACT_FROM = (NbfcRelevance.MEDIUM, NbfcRelevance.HIGH)
EXAMPLES = 8  # approved past decisions shown to the model (spec: 5-10)


@dataclass
class AgentSummary:
    enabled: bool = True
    screened: int = 0
    extracted: int = 0
    swept: int = 0
    disagreements: int = 0
    possible_misses: int = 0
    failures: int = 0
    stopped_because: str | None = None  # "budget" | "unavailable"
    problems: list[str] = field(default_factory=list)


class AgentOrchestrator:
    def __init__(
        self,
        repo: Repository,
        agent_factory: Callable[[], LunaAgent],
        *,
        enabled: Callable[[], bool] | None = None,
        max_workers: int = MAX_WORKERS,
    ) -> None:
        self._repo = repo
        self._factory = agent_factory
        self._agent: LunaAgent | None = None
        self._enabled = enabled or (lambda: settings.agent_enabled(repo))
        self._workers = max_workers

    def is_enabled(self) -> bool:
        return self._enabled()

    # --- a run -------------------------------------------------------------------------------

    def process(self, run_id: str, events: list[Any]) -> AgentSummary:
        """Screen / extract / sweep after a crawl. Never raises into the run."""
        summary = AgentSummary(enabled=self.is_enabled())
        if not summary.enabled:
            return summary
        try:
            self._process(run_id, events, summary)
        except Exception as exc:  # Luna must never break a run
            log.exception("Luna failed for run %s", run_id)
            summary.problems.append(f"Luna stopped: {exc}")
        self._report(run_id, summary)
        return summary

    def _process(self, run_id: str, events: list[Any], summary: AgentSummary) -> None:
        agent = self._get_agent()
        budget = Budget(todays_spend(self._repo), settings.daily_budget(self._repo))
        guidance, guidance_version = settings.guidance(self._repo)
        examples = self._examples()
        stop = _Stop()

        targets = [e for e in events if e.change in SCREENED]
        screened = self._run_pool(
            targets,
            lambda e: self._screen(
                agent, e, guidance, guidance_version, examples, budget, stop, run_id
            ),
        )
        medium_up: list[tuple[Any, Screened]] = []
        for event, result in screened:
            self._persist_calls(result.calls, summary, stop)
            if not result.usable:
                continue
            summary.screened += 1
            self._save_screen(event.item, result, guidance_version, run_id, summary)
            if result.relevance in EXTRACT_FROM:
                medium_up.append((event, result))

        extracted = self._run_pool(
            medium_up,
            lambda pair: self._extract(agent, pair[0], guidance_version, budget, stop, run_id),
        )
        for (event, _screen), result in extracted:
            self._persist_calls(result.calls, summary, stop)
            if result.usable:
                summary.extracted += 1
                self._save_extract(event.item, result, guidance_version)

        self._sweep(agent, run_id, guidance_version, budget, stop, summary)
        if stop.reason:
            summary.stopped_because = stop.reason

    # --- on demand ---------------------------------------------------------------------------

    def ask(self, item_id: str) -> AgentSummary:
        """ "Ask Luna": screen and extract one item now, whatever its relevance."""
        summary = AgentSummary(enabled=self.is_enabled())
        item = self._repo.get_item(item_id)
        if not summary.enabled or item is None:
            return summary
        agent = self._get_agent()
        budget = Budget(todays_spend(self._repo), settings.daily_budget(self._repo))
        guidance, version = settings.guidance(self._repo)
        stop = _Stop()
        event = _AskEvent(item, self._latest_change(item))
        screen = self._screen(agent, event, guidance, version, self._examples(), budget, stop, None)
        self._persist_calls(screen.calls, summary, stop)
        if screen.usable:
            summary.screened = 1
            self._save_screen(item, screen, version, None, summary)
        extract = self._extract(agent, event, version, budget, stop, None)
        self._persist_calls(extract.calls, summary, stop)
        if extract.usable:
            summary.extracted = 1
            self._save_extract(item, extract, version)
        summary.stopped_because = stop.reason
        return summary

    # --- one call each -----------------------------------------------------------------------

    def _screen(self, agent, event, guidance, version, examples, budget, stop, run_id) -> Screened:  # type: ignore[no-untyped-def]
        if stop.reason:
            return Screened(AgentStatus.ERROR)
        item = event.item
        source = self._repo.get_source(item.source_id)
        result = agent.screen(
            title=item.title,
            authority=source.authority if source else item.source_id,
            source_name=source.page_name if source else "",
            change=event.change.value if event.change else "NEW",
            text=self._text(item, event),
            guidance=guidance,
            guidance_version=version,
            examples=examples,
            budget=budget,
            item_id=item.id,
            run_id=run_id,
        )
        stop.observe(result.calls)
        return result

    def _extract(self, agent, event, version, budget, stop, run_id):  # type: ignore[no-untyped-def]
        from app.agents.luna.agent import Extracted

        if stop.reason:
            return Extracted(AgentStatus.ERROR)
        item = event.item
        source = self._repo.get_source(item.source_id)
        shown = item.displayed_date.isoformat() if item.displayed_date else "not shown"
        listing = (
            f"Title: {item.title}\nDate shown on the listing: {shown}\nLink: {item.canonical_url}"
        )
        result = agent.extract(
            title=item.title,
            authority=source.authority if source else item.source_id,
            listing_text=listing,
            text=self._text(item, event),
            guidance_version=version,
            budget=budget,
            item_id=item.id,
            run_id=run_id,
        )
        stop.observe(result.calls)
        return result

    def _text(self, item: RegulatoryItem, event: Any) -> str:
        """The item's document text; else the feed description; else just the title."""
        for doc in self._repo.list_documents(item.id):
            if doc.text_path:
                text = self._repo.read_text(doc.id)
                if text:
                    return text
        row = getattr(event, "row", None)
        description = row.raw_extra.get("description_html") if row is not None else None
        return html_to_text(description) if description else item.title

    def _latest_change(self, item: RegulatoryItem) -> ChangeType | None:
        obs = self._repo.list_observations(item.id)
        return obs[-1].change_type if obs else None

    def _examples(self) -> list[str]:
        """A few approved past decisions, so Luna sees how Compliance has judged similar items."""
        lines: list[str] = []
        for item in reversed(self._repo.list_items(_all())):
            if item.state not in (ItemState.DECIDED, ItemState.PUBLISHED):
                continue
            decisions = [d for d in self._repo.list_decisions(item.id) if not d.is_draft]
            if not decisions or decisions[-1].treatment is None:
                continue
            d = decisions[-1]
            lines.append(
                f"“{item.title}” -> treatment {d.treatment.value}, "
                f"applicable {d.applicability.value if d.applicability else 'not stated'}, "
                f"{'included' if d.include_in_update else 'not included'} in the update"
            )
            if len(lines) >= EXAMPLES:
                break
        return lines

    # --- the sweep ---------------------------------------------------------------------------

    def _sweep(self, agent, run_id, version, budget, stop, summary) -> None:  # type: ignore[no-untyped-def]
        done = {r.snapshot_id for r in self._repo.list_agent_runs() if r.task == AgentTask.SWEEP}
        jobs = []
        for check in self._repo.list_source_checks(run_id):
            if check.channel.value != "HTML" or not check.snapshot_id or check.snapshot_id in done:
                continue
            source = self._repo.get_source(check.source_id)
            snap = self._repo.get_snapshot(check.snapshot_id)
            if source is None or snap is None or source.type == SourceType.STRUCTURED_DATA:
                continue
            blob = self._repo.read_blob(snap.sha256)
            if blob is None:
                continue
            titles = [r.title for r in self._repo.list_raw_items(run_id, source.id)]
            jobs.append(
                (source, snap, html_to_text(blob.decode("utf-8", errors="replace")), titles)
            )
        results = self._run_pool(
            jobs, lambda j: self._sweep_one(agent, j, version, budget, stop, run_id)
        )
        for job, swept in results:
            if swept is None:
                continue
            self._persist_calls(swept.calls, summary, stop)
            if swept.status in (AgentStatus.ERROR, AgentStatus.BUDGET, AgentStatus.INVALID_JSON):
                continue
            summary.swept += 1
            source, snap = job[0], job[1]
            existing = {
                f.payload.get("title", "").casefold()
                for f in self._repo.list_agent_flags(source_id=source.id, status=FlagStatus.OPEN)
            }
            flags = [
                AgentFlag(
                    id=f"flg-{uuid.uuid4().hex[:10]}",
                    run_id=run_id,
                    source_id=source.id,
                    snapshot_id=snap.id,
                    type=FlagType.POSSIBLE_MISS,
                    payload=miss,
                    created_at=now_ist(),
                )  # fmt: skip
                for miss in swept.misses
                if miss["title"].casefold() not in existing
            ]
            self._repo.add_agent_flags(flags, ACTOR)
            summary.possible_misses += len(flags)

    def _sweep_one(self, agent, job, version, budget, stop, run_id):  # type: ignore[no-untyped-def]
        if stop.reason:
            return None
        source, snap, text, titles = job
        result = agent.sweep(
            source_name=f"{source.authority} - {source.page_name}",
            snapshot_text=text,
            parsed_titles=titles,
            guidance_version=version,
            budget=budget,
            snapshot_id=snap.id,
            run_id=run_id,
        )
        stop.observe(result.calls)
        return result

    # --- storing ---------------------------------------------------------------------------------

    def _save_screen(self, item, result: Screened, version, run_id, summary) -> None:  # type: ignore[no-untyped-def]
        self._repo.save_extraction(
            AiExtraction(
                id=f"aix-{uuid.uuid4().hex[:10]}",
                item_id=item.id,
                task=AgentTask.SCREEN,
                fields={
                    "addressed_to": result.addressed_to,
                    "reason": result.reason,
                    "urgency": result.urgency,
                    "evidence": result.evidence,
                    "rejected_quotes": result.rejected_quotes,
                },
                nbfc_relevance=result.relevance,
                relevance_score=result.score,
                suggested_treatment=result.treatment,
                suggested_sme_function=result.sme_function,
                model=result.calls[0].record.model,
                deployment=result.calls[0].record.deployment,
                prompt_version=result.calls[0].record.prompt_version,
                guidance_version=version,
                created_at=now_ist(),
            ),
            True,  # the item may move DETECTED -> ENRICHED, nothing further
            ACTOR,
        )
        threshold = int((self._repo.get_setting("low-relevance-threshold") or _Default(30)).value)
        rule = settings.rule_level(item.relevance_score, threshold)
        luna = result.relevance.value if result.relevance else "NONE"
        if settings.levels_apart(rule, luna) >= 2 and not self._repo.list_agent_flags(
            item_id=item.id, status=FlagStatus.OPEN
        ):
            self._repo.add_agent_flags(
                [
                    AgentFlag(
                        id=f"flg-{uuid.uuid4().hex[:10]}",
                        run_id=run_id,
                        source_id=item.source_id,
                        item_id=item.id,
                        type=FlagType.DISAGREEMENT,
                        payload={
                            "rule_level": rule,
                            "luna_level": luna,
                            "rule_score": item.relevance_score,
                            "luna_score": result.score,
                        },
                        created_at=now_ist(),
                    )  # fmt: skip
                ],
                ACTOR,
            )
            summary.disagreements += 1

    def _save_extract(self, item, result, version) -> None:  # type: ignore[no-untyped-def]
        record = result.calls[0].record
        self._repo.save_extraction(
            AiExtraction(
                id=f"aix-{uuid.uuid4().hex[:10]}",
                item_id=item.id,
                task=AgentTask.EXTRACT,
                fields=result.fields,
                model=record.model,
                deployment=record.deployment,
                prompt_version=record.prompt_version,
                guidance_version=version,
                created_at=now_ist(),
            ),
            False,
            ACTOR,
        )

    def _persist_calls(self, calls, summary: AgentSummary, stop: "_Stop") -> None:  # type: ignore[no-untyped-def]
        for call in calls:
            self._repo.add_agent_run(call.record, ACTOR)
            if call.record.status in (AgentStatus.ERROR, AgentStatus.INVALID_JSON):
                summary.failures += 1

    def _report(self, run_id: str, summary: AgentSummary) -> None:
        """An alert when Luna could not do its work; the run and its items carry on."""
        message = None
        if summary.stopped_because == "budget":
            message = (
                "Luna unavailable: today's budget is used up. Items continue without suggestions."
            )
        elif summary.stopped_because == "unavailable":
            message = (
                "Luna unavailable: the model did not answer. Items continue without suggestions."
            )
        elif summary.problems:
            message = "; ".join(summary.problems)
        if message:
            self._repo.add_alerts(
                [
                    Alert(
                        id=f"alr-luna-{uuid.uuid4().hex[:8]}",
                        run_id=run_id,
                        kind=AlertKind.RUN_PROBLEM,
                        severity="warning",
                        message=message,
                        created_at=now_ist(),
                    )  # fmt: skip
                ],
                ACTOR,
            )

    # --- plumbing --------------------------------------------------------------------------------

    def _get_agent(self) -> LunaAgent:
        if self._agent is None:
            self._agent = self._factory()
        return self._agent

    def _run_pool(self, items: list[Any], work: Callable[[Any], Any]) -> list[tuple[Any, Any]]:
        """Up to four calls at a time; results come back in the order the items were given."""
        if not items:
            return []
        with ThreadPoolExecutor(max_workers=self._workers) as pool:
            futures = {pool.submit(work, item): i for i, item in enumerate(items)}
            done: dict[int, Any] = {}
            for future in as_completed(futures):
                try:
                    done[futures[future]] = future.result()
                except Exception as exc:  # one item failing never stops the others
                    log.exception("Luna call failed")
                    done[futures[future]] = _Failed(str(exc))
        return [(items[i], done[i]) for i in sorted(done)]


class _Stop:
    """Shared flag, set as soon as the budget is gone or the provider keeps failing, so the calls
    still waiting in the pool are skipped."""

    def __init__(self) -> None:
        self.reason: str | None = None
        self._errors = 0
        self._lock = threading.Lock()

    def trip(self, reason: str) -> None:
        with self._lock:
            self.reason = self.reason or reason

    def observe(self, calls) -> None:  # type: ignore[no-untyped-def]
        """Look at finished calls (called from the worker, right after each call)."""
        for call in calls:
            status = call.record.status
            if status == AgentStatus.BUDGET:
                self.trip("budget")
            elif status in (AgentStatus.ERROR, AgentStatus.INVALID_JSON):
                with self._lock:
                    self._errors += 1
                    too_many = self._errors >= MAX_CONSECUTIVE_ERRORS
                if too_many:
                    self.trip("unavailable")
            else:
                with self._lock:
                    self._errors = 0


@dataclass
class _AskEvent:
    item: RegulatoryItem
    change: ChangeType | None
    row: Any = None


@dataclass
class _Default:
    value: Any


@dataclass
class _Failed:
    detail: str
    calls: list[Any] = field(default_factory=list)
    usable: bool = False
    status: AgentStatus = AgentStatus.ERROR
    misses: list[Any] = field(default_factory=list)


def _all():  # type: ignore[no-untyped-def]
    from app.schemas.api import ItemFilter

    return ItemFilter()
