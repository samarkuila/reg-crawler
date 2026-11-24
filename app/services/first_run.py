"""The first-crawl window: how far back a source's FIRST successful crawl stores items.

Items older than the window are not stored as items. Only their fingerprint is recorded
("already seen"), so later crawls can tell a known-old item from a genuinely new one. Nothing is
hidden: every older row is counted in the run log (CLAUDE.md rule 3).

Config is data: `crawler.first_run` in config/settings.yaml, with optional per-source overrides
(`first_run_mode`, `first_run_days`, `sorted_newest_first`) that go through the normal versioned
maker-checker path.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.entities import Source
from app.schemas.enums import FirstRunMode

ROOT = Path(__file__).resolve().parents[2]
SETTINGS_FILE = ROOT / "config" / "settings.yaml"


class FirstRunConfig(BaseModel):
    """`crawler.first_run` in settings.yaml. Unknown keys are refused so a typo is not ignored."""

    model_config = ConfigDict(extra="forbid")

    mode: FirstRunMode = FirstRunMode.CURRENT_MONTH
    last_n_days: int = Field(30, ge=1, le=3650)  # used when mode is last_n_days
    min_lookback_days: int = Field(7, ge=1, le=3650)  # current_month never reaches back less
    timezone: str = "Asia/Kolkata"
    undated_max_items: int = Field(20, ge=0, le=10_000)  # a source with no dates keeps the newest N
    baseline_items_in_review: bool = False  # True: baseline items also appear in the review queue


def load_first_run_config(path: Path | None = None) -> FirstRunConfig:
    """The `crawler.first_run` block (defaults when the file or block is missing)."""
    try:
        data: Any = yaml.safe_load((path or SETTINGS_FILE).read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return FirstRunConfig()
    block = ((data or {}).get("crawler") or {}).get("first_run") if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return FirstRunConfig()
    try:
        return FirstRunConfig.model_validate(block)
    except ValidationError as exc:
        raise ValueError(f"crawler.first_run in settings.yaml is not valid: {exc}") from exc


def today_in(tz: str, now: datetime | None = None) -> date:
    """Today's calendar date in `tz` (Asia/Kolkata by default), never the UTC date."""
    zone = ZoneInfo(tz)
    return (now.astimezone(zone) if now else datetime.now(zone)).date()


def window_start(
    today: date,
    mode: FirstRunMode | str,
    last_n_days: int,
    min_lookback_days: int,
    tz: str = "Asia/Kolkata",  # noqa: ARG001 - `today` is already a calendar date in this zone
) -> date | None:
    """The oldest date a first crawl stores as an item. None means "no limit" (mode all).

    current_month: the 1st of this month, but never fewer than `min_lookback_days` back.
    last_n_days:   today minus N days.
    """
    mode = FirstRunMode(mode)
    if mode == FirstRunMode.ALL:
        return None
    if mode == FirstRunMode.LAST_N_DAYS:
        return today - timedelta(days=last_n_days)
    return min(today.replace(day=1), today - timedelta(days=min_lookback_days))


@dataclass(frozen=True)
class Window:
    start: date | None  # None = store everything
    mode: FirstRunMode
    days: int


def window_for(source: Source, cfg: FirstRunConfig, today: date | None = None) -> Window:
    """The window for one source: its own override if it has one, else the global setting."""
    mode = source.first_run_mode or cfg.mode
    days = source.first_run_days or cfg.last_n_days
    day = today or today_in(cfg.timezone)
    return Window(window_start(day, mode, days, cfg.min_lookback_days, cfg.timezone), mode, days)
