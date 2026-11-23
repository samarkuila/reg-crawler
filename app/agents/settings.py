"""Whether Luna is on, and its daily budget.

Two switches, both must allow it: the environment flag AGENT_LUNA_ENABLED (off by default; set by
whoever deploys) and the `agent-enabled` setting (editable on the Settings page, needs approval).
The system works fully with it off (CLAUDE.md rule 11).
"""

import os

from app.repositories.base import Repository

DEFAULT_BUDGET_INR = 200.0
LEVELS = ["NONE", "LOW", "MEDIUM", "HIGH"]  # lowest to highest


def env_enabled() -> bool:
    return os.environ.get("AGENT_LUNA_ENABLED", "false").strip().lower() == "true"


def agent_enabled(repo: Repository) -> bool:
    if not env_enabled():
        return False
    setting = repo.get_setting("agent-enabled")
    return True if setting is None else bool(setting.value)


def daily_budget(repo: Repository) -> float:
    setting = repo.get_setting("agent-daily-budget-inr")
    if setting is not None and setting.value is not None:
        return float(setting.value)
    return float(os.environ.get("AGENT_DAILY_BUDGET_INR") or DEFAULT_BUDGET_INR)


def guidance(repo: Repository) -> tuple[str, int | None]:
    """(screening guidance text, its setting version)."""
    setting = repo.get_setting("screening-guidance")
    if setting is None:
        return "", None
    value = setting.value
    text = value.get("text", "") if isinstance(value, dict) else str(value or "")
    return text, setting.version


def rule_level(score: int, threshold: int) -> str:
    """Our keyword score as one of Luna's four levels (so the two can be compared)."""
    if score >= 70:
        return "HIGH"
    if score >= 50:
        return "MEDIUM"
    if score >= threshold:
        return "LOW"
    return "NONE"


def levels_apart(rule: str, luna: str) -> int:
    return abs(LEVELS.index(rule) - LEVELS.index(luna))
