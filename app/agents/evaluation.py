"""Agent evaluation (agent spec §9): run Luna on a labelled golden set and measure it.

    python -m app.cli agent-eval --golden tests/fixtures/agent/golden.jsonl

Writes docs/agent-eval-report.md with: recall of NBFC-relevant items scored MEDIUM/HIGH,
precision of HIGH, quote validity, field accuracy and cost. Any change to a prompt or the
screening guidance should re-run it (policy A6).

`--fake` uses a simple keyword stand-in instead of a model. It only proves the plumbing works; the
report then says clearly that its numbers say nothing about Luna.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.agents.luna.agent import PROMPT_VERSION, LunaAgent
from app.agents.luna.schemas import EXTRACT_FIELDS
from app.schemas.enums import NbfcRelevance
from app.services.llm import Budget, FakeLlmClient

TARGETS = {
    "recall": 0.98,
    "precision_high": 0.80,
    "quote_validity": 1.0,
    "field_accuracy": 0.95,
}
CHECKED_FIELDS = ("ref_no", "issue_date", "effective_date", "authority")
UP = (NbfcRelevance.MEDIUM, NbfcRelevance.HIGH)


@dataclass
class GoldenItem:
    id: str
    title: str
    authority: str
    text: str
    nbfc_relevant: bool
    expected_level: str
    expected: dict[str, Any]


def load_golden(path: Path) -> list[GoldenItem]:
    items = []
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            items.append(GoldenItem(**json.loads(line)))
        except (ValueError, TypeError) as exc:
            raise ValueError(f"{path}:{n}: not a valid golden item ({exc})") from exc
    if not items:
        raise ValueError(f"{path} has no items")
    return items


@dataclass
class ItemResult:
    item: GoldenItem
    level: str | None
    returned_quotes: int = 0
    rejected_quotes: int = 0
    fields_checked: int = 0
    fields_correct: int = 0
    cost: float = 0.0
    note: str = ""


@dataclass
class Report:
    results: list[ItemResult] = field(default_factory=list)
    client: str = ""
    model: str = ""
    guidance_version: int | None = None
    fake: bool = False

    # --- metrics --------------------------------------------------------------------------------
    def recall(self) -> float | None:
        relevant = [r for r in self.results if r.item.nbfc_relevant]
        if not relevant:
            return None
        hit = sum(1 for r in relevant if r.level in ("MEDIUM", "HIGH"))
        return hit / len(relevant)

    def precision_high(self) -> float | None:
        high = [r for r in self.results if r.level == "HIGH"]
        if not high:
            return None
        return sum(1 for r in high if r.item.nbfc_relevant) / len(high)

    def quote_validity(self) -> float | None:
        """Share of returned quotes that were really in the text. Rejected ones were nulled, so
        nothing unvalidated is ever shown; this number says how often the model invents."""
        returned = sum(r.returned_quotes for r in self.results)
        if not returned:
            return None
        return 1 - sum(r.rejected_quotes for r in self.results) / returned

    def field_accuracy(self) -> float | None:
        checked = sum(r.fields_checked for r in self.results)
        return None if not checked else sum(r.fields_correct for r in self.results) / checked

    def cost(self) -> float:
        return sum(r.cost for r in self.results)


def evaluate(
    agent: LunaAgent, items: list[GoldenItem], guidance: str, version: int | None
) -> Report:
    report = Report(guidance_version=version)
    for g in items:
        budget = Budget(0.0, None)
        screened = agent.screen(
            title=g.title,
            authority=g.authority,
            source_name=g.authority,
            change="NEW",
            text=g.text,
            guidance=guidance,
            guidance_version=version,
            examples=[],
            budget=budget,
            item_id=g.id,
        )
        result = ItemResult(g, screened.relevance.value if screened.relevance else None)
        result.returned_quotes += len(screened.evidence) + screened.rejected_quotes
        result.rejected_quotes += screened.rejected_quotes
        calls = list(screened.calls)
        if screened.relevance in UP or (screened.relevance is None and g.nbfc_relevant):
            ext = agent.extract(
                title=g.title,
                authority=g.authority,
                listing_text=f"Title: {g.title}\nDate shown on the listing: not shown",
                text=g.text,
                guidance_version=version,
                budget=budget,
                item_id=g.id,
            )
            calls += ext.calls
            raw = ext.calls[0].result.data or {}
            for name in EXTRACT_FIELDS:
                sent = (raw.get(name) or {}) if isinstance(raw, dict) else {}
                if sent.get("value") is not None:
                    result.returned_quotes += 1
                    if not ext.fields.get(name, {}).get("validated"):
                        result.rejected_quotes += 1
            for name in CHECKED_FIELDS:
                got = ext.fields.get(name, {})
                if got.get("validated") and got.get("value") is not None:
                    result.fields_checked += 1
                    expected = g.expected.get(name)
                    result.fields_correct += int(_same(got["value"], expected))
        result.cost = sum(c.result.cost_inr for c in calls)
        if screened.relevance is None:
            result.note = f"no usable answer ({screened.status.value})"
        report.results.append(result)
        if calls:
            report.model = calls[0].record.model
    return report


def _same(a: Any, b: Any) -> bool:
    return str(a).strip().casefold() == str(b).strip().casefold()


# --- the report file ---------------------------------------------


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.0f}%"


def _verdict(name: str, value: float | None, report: Report) -> str:
    if report.fake:
        return "not meaningful (fake client)"
    if value is None:
        return "no data"
    return "pass" if value >= TARGETS[name] else "FAIL"


def write_report(report: Report, path: Path, golden: Path, size_note: str) -> None:
    rows = [
        ("Recall on NBFC-relevant items (MEDIUM/HIGH)", "recall", report.recall(), "≥ 98%"),
        ("Precision of HIGH", "precision_high", report.precision_high(), "≥ 80%"),
        (
            "Quote validity (returned quotes found in the text)",
            "quote_validity",
            report.quote_validity(),
            "100%",
        ),
        (
            "Field accuracy (validated ref no., dates, authority)",
            "field_accuracy",
            report.field_accuracy(),
            "≥ 95%",
        ),
    ]
    lines = [
        "# Luna evaluation report",
        "",
        f"Generated {date.today().strftime('%d.%m.%Y')} · golden set `{golden.as_posix()}` "
        f"({len(report.results)} items) · prompt {PROMPT_VERSION} · "
        f"guidance version {report.guidance_version or '-'} · model {report.model or '-'} · "
        f"client: {report.client}",
        "",
    ]
    if report.fake:
        lines += [
            "> **FAKE CLIENT: these numbers prove the evaluation runs, not that Luna works.** "
            "Run again against the approved endpoint (no `--fake`) for a real result.",
            "",
        ]
    if size_note:
        lines += [f"> {size_note}", ""]
    lines += ["| Metric | Result | Target | Verdict |", "|---|---|---|---|"]
    for label, key, value, target in rows:
        lines.append(f"| {label} | {_pct(value)} | {target} | {_verdict(key, value, report)} |")
    lines += [
        f"| Cost | ₹{report.cost():.2f} for {len(report.results)} items "
        f"(₹{report.cost() / max(len(report.results), 1):.2f} each) | within the daily budget "
        "| see Settings |",
        "",
        "Sweep false alarms (≤ 2 a day across 5 sources) can only be measured in the shadow run.",
        "",
        "## Per item",
        "",
        "| Id | Title | Expected | Luna | Quotes (rejected) | Fields right | Note |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report.results:
        lines.append(
            f"| {r.item.id} | {r.item.title[:60]} | {r.item.expected_level} | {r.level or '-'} | "
            f"{r.returned_quotes} ({r.rejected_quotes}) | "
            f"{r.fields_correct}/{r.fields_checked} | {r.note} |"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# --- a keyword stand-in for plumbing checks ----------------------


def heuristic_client() -> FakeLlmClient:
    """No model: a keyword rule that answers in Luna's format. Only for `agent-eval --fake`."""

    def text_of(user: str) -> str:
        m = re.search(r"<untrusted_document>\n(.*?)\n</untrusted_document>", user, re.S)
        return m.group(1) if m else ""

    def title_of(user: str) -> str:
        m = re.search(r"- Title: (.+)", user)
        return m.group(1).strip() if m else ""

    def screen(system: str, user: str) -> dict[str, Any]:
        text = text_of(user)
        relevant = re.search(
            r"\bNBFC|non-banking|regulated entities|lending|Financial institutions", text, re.I
        )
        level = "HIGH" if relevant else "NONE"
        return {
            "nbfc_relevance": level,
            "relevance_score": 0.9 if relevant else 0.05,
            "addressed_to": ["NBFCs"] if relevant else [],
            "reason": "Keyword stand-in.",
            "suggested_treatment": "INFO_ONLY",
            "suggested_sme_function": "None",
            "urgency": "NORMAL",
            "evidence": [{"quote": title_of(user), "location": "title"}],
        }

    def extract(system: str, user: str) -> dict[str, Any]:
        text = text_of(user)
        null = {"value": None, "evidence_quote": None, "location": None, "confidence": None}
        out = {name: dict(null) for name in EXTRACT_FIELDS}
        ref = re.search(r"\b([A-Z]{2,5}[./][\w./()-]*\d[\w./()-]*)", text)
        if ref:
            out["ref_no"] = {
                "value": ref.group(1).rstrip("."),
                "evidence_quote": ref.group(1).rstrip("."),
                "location": "text",
                "confidence": 0.5,
            }
        return out

    return FakeLlmClient({"screen": screen, "extract": extract}, cost_inr=0.0)
