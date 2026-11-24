"""agent-eval: golden set loading, metrics, report file, and the CLI (fake client only)."""

import json
from pathlib import Path

import pytest

from app.agents import evaluation
from app.agents.luna.agent import LunaAgent
from app.cli import main
from app.services.llm import FakeLlmClient, LoggedLlm
from tests.unit.test_luna_agent import extract_answer, screen_answer

GOLDEN = Path(__file__).resolve().parents[1] / "fixtures" / "agent" / "golden.jsonl"


def test_the_golden_file_is_valid_and_labelled() -> None:
    items = evaluation.load_golden(GOLDEN)
    assert len(items) >= 10
    assert {i.expected_level for i in items} <= {"HIGH", "MEDIUM", "LOW", "NONE"}
    assert any(i.nbfc_relevant for i in items) and any(not i.nbfc_relevant for i in items)


def test_a_bad_golden_line_names_the_line(tmp_path) -> None:
    bad = tmp_path / "g.jsonl"
    bad.write_text('{"id": "x"}\n', encoding="utf-8")
    with pytest.raises(ValueError, match=r"g\.jsonl:1"):
        evaluation.load_golden(bad)
    empty = tmp_path / "e.jsonl"
    empty.write_text("\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no items"):
        evaluation.load_golden(empty)


def run(script):
    items = evaluation.load_golden(GOLDEN)
    return evaluation.evaluate(LunaAgent(LoggedLlm(FakeLlmClient(script))), items, "guidance", 3)


def echo_screen(by_id: dict):
    """Luna rates each golden item as `by_id` says; its quote is the item's own title."""

    def answer(system: str, user: str):
        title = user.split("- Title: ")[1].splitlines()[0].strip()
        level = by_id(title)
        return screen_answer(nbfc_relevance=level, evidence=[{"quote": title, "location": "title"}])

    return answer


def test_perfect_screening_scores_full_marks() -> None:
    items = {i.title: i.expected_level for i in evaluation.load_golden(GOLDEN)}
    report = run({"screen": echo_screen(items.get), "extract": lambda s, u: extract_answer()})
    assert report.recall() == 1.0 and report.precision_high() is not None
    assert report.quote_validity() == pytest.approx(1.0, abs=0.7)  # extract quotes are generic here


def test_recall_counts_relevant_items_rated_below_medium() -> None:
    items = evaluation.load_golden(GOLDEN)
    relevant = {i.title for i in items if i.nbfc_relevant}
    report = run({"screen": echo_screen(lambda t: "LOW" if t in relevant else "NONE")})
    assert report.recall() == 0.0 and report.precision_high() is None


def test_precision_of_high_counts_wrongly_high_items() -> None:
    report = run({"screen": echo_screen(lambda t: "HIGH")})  # everything HIGH
    relevant = sum(1 for i in evaluation.load_golden(GOLDEN) if i.nbfc_relevant)
    assert report.precision_high() == pytest.approx(relevant / 12)


def test_fabricated_quotes_lower_quote_validity_but_are_nulled() -> None:
    def fabricated(system, user):
        return screen_answer(evidence=[{"quote": "never in any text", "location": "p"}])

    report = run({"screen": fabricated})
    assert report.quote_validity() == 0.0
    assert all(r.level is None for r in report.results)  # nothing unvalidated survives


def test_the_report_states_targets_verdicts_and_the_fake_warning(tmp_path) -> None:
    items = {i.title: i.expected_level for i in evaluation.load_golden(GOLDEN)}
    report = run({"screen": echo_screen(items.get), "extract": lambda s, u: extract_answer()})
    report.client, report.model = "fake", "gpt-6-luna"
    out = tmp_path / "report.md"
    evaluation.write_report(report, out, GOLDEN, "Only 12 items.")
    text = out.read_text(encoding="utf-8")
    needles = [
        "# Luna evaluation report", "Recall on NBFC-relevant", "≥ 98%", "Precision of HIGH",
        "Quote validity", "Field accuracy", "Cost", "## Per item", "Only 12 items.",
    ]  # fmt: skip
    for needle in needles:
        assert needle in text
    assert "pass" in text or "FAIL" in text
    report.fake = True
    evaluation.write_report(report, out, GOLDEN, "")
    assert "FAKE CLIENT" in out.read_text(encoding="utf-8")


def test_the_cli_runs_with_the_fake_client_and_writes_the_report(
    tmp_path, capsys, monkeypatch
) -> None:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'none.db').as_posix()}")
    out = tmp_path / "docs" / "report.md"
    code = main(["agent-eval", "--golden", str(GOLDEN), "--out", str(out), "--fake"])
    assert code == 0 and out.is_file()
    printed = capsys.readouterr().out
    assert "recall" in printed and "fake" in printed.lower()
    assert "FAKE CLIENT" in out.read_text(encoding="utf-8")


def test_the_cli_refuses_without_a_configured_endpoint(tmp_path, capsys, monkeypatch) -> None:
    for key in ("AWS_GPT_LUNA_ENDPOINT", "AWS_GPT_LUNA_KEY"):
        monkeypatch.delenv(key, raising=False)
    code = main(["agent-eval", "--golden", str(GOLDEN), "--out", str(tmp_path / "r.md")])
    assert code == 2 and "AWS_GPT_LUNA_KEY" in capsys.readouterr().err
    assert not (tmp_path / "r.md").exists()


def test_golden_lines_are_one_json_object_each() -> None:
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        assert isinstance(json.loads(line), dict)
