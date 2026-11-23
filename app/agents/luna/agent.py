"""Luna: screen, extract, sweep (agent spec §4). Advisory only.

What this module does and does not do (CLAUDE.md rule 11, policy §3):
- It builds a text-only request, calls the LLM through `LoggedLlm`, and returns suggestions.
- It has no tools, no browsing, and no access to the repository: it cannot change an item, a state
  or a decision. The orchestrator stores what it returns in ai_extraction / agent_flag / agent_run.
- Document and snapshot text is wrapped in <untrusted_document> and the closing tag is removed from
  inside it, so the text cannot break out of its block.
- The answer must match the schema exactly, and every quote must appear in the text that was sent
  (whitespace-normalised). A field whose quote fails is set to null and the call is recorded as
  QUOTE_REJECTED.
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.agents.luna import schemas
from app.agents.luna.evidence import Haystack
from app.schemas.enums import AgentStatus, AgentTask, NbfcRelevance
from app.services.llm import Budget, Call, LoggedLlm

PROMPT_VERSION = "v1"
PROMPTS = Path(__file__).parent / "prompts"
SCREEN_CHARS = 4_000  # spec T1: the first 4,000 characters
EXTRACT_CHARS = 20_000
SWEEP_CHARS = 30_000
OPEN, CLOSE = "<untrusted_document>", "</untrusted_document>"


def load_prompt(name: str) -> str:
    return (PROMPTS / f"{name}_{PROMPT_VERSION}.md").read_text(encoding="utf-8")


def wrap_untrusted(text: str) -> str:
    """Put text from a website or document in a data block it cannot close from the inside."""
    safe = text.replace(CLOSE, "[removed]").replace(OPEN, "[removed]")
    return f"{OPEN}\n{safe}\n{CLOSE}"


def _fill(template: str, **values: str) -> str:
    for key, value in values.items():
        template = template.replace(f"[[{key}]]", value)
    return template


# --- results -----------------------------------------------------------------------------------


@dataclass
class Screened:
    status: AgentStatus
    relevance: NbfcRelevance | None = None
    score: float | None = None
    addressed_to: list[str] = field(default_factory=list)
    reason: str | None = None
    treatment: str | None = None
    sme_function: str | None = None
    urgency: str | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)  # validated quotes only
    rejected_quotes: int = 0
    calls: list[Call] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return self.relevance is not None


@dataclass
class Extracted:
    status: AgentStatus
    fields: dict[str, dict[str, Any]] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    @property
    def usable(self) -> bool:
        return any(f.get("value") is not None for f in self.fields.values())


@dataclass
class Swept:
    status: AgentStatus
    misses: list[dict[str, Any]] = field(default_factory=list)
    rejected: int = 0
    calls: list[Call] = field(default_factory=list)


def _finish(call: Call, status: AgentStatus, detail: str | None = None) -> None:
    """Record the final status on the call's agent_run row (for example QUOTE_REJECTED)."""
    call.record.status = status
    if detail:
        call.record.detail = detail


def _bad_answer(call: Call, why: str) -> AgentStatus:
    _finish(call, AgentStatus.INVALID_JSON, why)
    return AgentStatus.INVALID_JSON


# --- the agent ---------------------------------------------------------------------------------


class LunaAgent:
    def __init__(self, llm: LoggedLlm) -> None:
        self._llm = llm

    # T1 -------------------------------------------------------------------------------------

    def screen(
        self,
        *,
        title: str,
        authority: str,
        source_name: str,
        change: str,
        text: str,
        guidance: str,
        guidance_version: int | None,
        examples: list[str],
        budget: Budget,
        item_id: str | None = None,
        run_id: str | None = None,
    ) -> Screened:
        sent = text[:SCREEN_CHARS]
        user = _fill(
            load_prompt("screen"),
            guidance=guidance or "(none)",
            guidance_version=str(guidance_version or "-"),
            examples="\n".join(f"- {e}" for e in examples) or "(none)",
            title=title,
            authority=authority,
            source=source_name,
            change=change,
            document=wrap_untrusted(sent),
        )
        call = self._llm.call(
            budget=budget, task=AgentTask.SCREEN, prompt_version=PROMPT_VERSION,
            guidance_version=guidance_version, system=load_prompt("system"), user=user,
            schema=schemas.SCREEN_SCHEMA, effort="low", run_id=run_id, item_id=item_id,
        )  # fmt: skip
        out = Screened(call.result.status, calls=[call])
        if call.result.data is None:
            return out
        try:
            schemas.validate(call.result.data, schemas.SCREEN_SCHEMA)
        except schemas.SchemaError as exc:
            out.status = _bad_answer(call, str(exc)[:200])
            return out
        data = call.result.data
        haystack = Haystack(title, sent)  # the title is part of the text we sent
        good = [e for e in data["evidence"] if haystack.has(e["quote"])]
        out.rejected_quotes = len(data["evidence"]) - len(good)
        if not good:
            # Policy A3: nothing the model said is backed by the text, so nothing is kept.
            out.status = AgentStatus.QUOTE_REJECTED
            _finish(call, out.status, "No quote was found in the text; every field is null")
            return out
        out.relevance = NbfcRelevance(data["nbfc_relevance"])
        out.score = max(0.0, min(1.0, float(data["relevance_score"])))
        out.addressed_to = list(data["addressed_to"])
        out.reason = data["reason"].strip() or None
        out.treatment = data["suggested_treatment"]
        out.sme_function = (
            None if data["suggested_sme_function"] == "None" else data["suggested_sme_function"]
        )
        out.urgency = data["urgency"]
        out.evidence = [{**e, "validated": True} for e in good]
        if out.rejected_quotes:
            out.status = AgentStatus.QUOTE_REJECTED
            _finish(
                call,
                out.status,
                f"{out.rejected_quotes} quote(s) were not in the text and were dropped",
            )
        return out

    # T2 -------------------------------------------------------------------------------------

    def extract(
        self,
        *,
        title: str,
        authority: str,
        listing_text: str,
        text: str,
        guidance_version: int | None,
        budget: Budget,
        item_id: str | None = None,
        run_id: str | None = None,
    ) -> Extracted:
        sent = text[:EXTRACT_CHARS]
        user = _fill(
            load_prompt("extract"),
            title=title,
            authority=authority,
            listing=wrap_untrusted(listing_text),
            document=wrap_untrusted(sent),
        )
        call = self._llm.call(
            budget=budget, task=AgentTask.EXTRACT, prompt_version=PROMPT_VERSION,
            guidance_version=guidance_version, system=load_prompt("system"), user=user,
            schema=schemas.EXTRACT_SCHEMA, effort="medium", run_id=run_id, item_id=item_id,
        )  # fmt: skip
        out = Extracted(call.result.status, calls=[call])
        if call.result.data is None:
            return out
        try:
            schemas.validate(call.result.data, schemas.EXTRACT_SCHEMA)
        except schemas.SchemaError as exc:
            out.status = _bad_answer(call, str(exc)[:200])
            return out
        haystack = Haystack(title, listing_text, sent)
        rejected = 0
        for name in schemas.EXTRACT_FIELDS:
            raw = call.result.data[name]
            checked, was_rejected = self._check_field(name, raw, haystack)
            out.fields[name] = checked
            rejected += was_rejected
        if rejected:
            out.status = AgentStatus.QUOTE_REJECTED
            _finish(
                call, out.status, f"{rejected} field(s) had no verbatim quote and were set to null"
            )
        return out

    @staticmethod
    def _check_field(
        name: str, raw: dict[str, Any], haystack: Haystack
    ) -> tuple[dict[str, Any], int]:
        """(field, 1 if a value was thrown away). Without a verbatim quote the value is null."""
        empty = {"value": None, "evidence_quote": None, "location": None, "confidence": None,
                 "validated": False}  # fmt: skip
        if raw["value"] is None:
            return empty, 0
        if not haystack.has(raw["evidence_quote"]):
            return empty, 1
        if name in schemas.DATE_FIELDS:
            try:
                date.fromisoformat(str(raw["value"]))
            except ValueError:
                return empty, 1
        confidence = raw["confidence"]
        return {
            "value": raw["value"],
            "evidence_quote": raw["evidence_quote"],
            "location": raw["location"],
            "confidence": None if confidence is None else max(0.0, min(1.0, float(confidence))),
            "validated": True,
        }, 0

    # T3 -------------------------------------------------------------------------------------

    def sweep(
        self,
        *,
        source_name: str,
        snapshot_text: str,
        parsed_titles: list[str],
        guidance_version: int | None,
        budget: Budget,
        snapshot_id: str | None = None,
        run_id: str | None = None,
    ) -> Swept:
        sent = snapshot_text[:SWEEP_CHARS]
        user = _fill(
            load_prompt("sweep"),
            source=source_name,
            parsed_items="\n".join(f"- {t}" for t in parsed_titles) or "(none)",
            snapshot=wrap_untrusted(sent),
        )
        call = self._llm.call(
            budget=budget, task=AgentTask.SWEEP, prompt_version=PROMPT_VERSION,
            guidance_version=guidance_version, system=load_prompt("system"), user=user,
            schema=schemas.SWEEP_SCHEMA, effort="low", run_id=run_id, snapshot_id=snapshot_id,
        )  # fmt: skip
        out = Swept(call.result.status, calls=[call])
        if call.result.data is None:
            return out
        try:
            schemas.validate(call.result.data, schemas.SWEEP_SCHEMA)
        except schemas.SchemaError as exc:
            out.status = _bad_answer(call, str(exc)[:200])
            return out
        haystack = Haystack(sent)
        known = [t.casefold().strip() for t in parsed_titles]
        for miss in call.result.data["possible_misses"]:
            title = miss["title"].casefold().strip()
            if not haystack.has(miss["evidence_quote"]):
                out.rejected += 1  # invented or altered: dropped
            elif title and any(title in k or k in title for k in known if k):
                continue  # the parser already has it
            else:
                out.misses.append({**miss, "validated": True})
        if out.rejected:
            out.status = AgentStatus.QUOTE_REJECTED
            _finish(call, out.status, f"{out.rejected} possible miss(es) had no verbatim quote")
        return out
