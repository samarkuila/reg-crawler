"""Crawler configuration read from config/settings.yaml (config is data, not code)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
SETTINGS_FILE = ROOT / "config" / "settings.yaml"

DEFAULT_BLOCK_STATUS_CODES = (401, 403, 429)


@dataclass(frozen=True)
class CrawlerConfig:
    contact: str
    block_markers: tuple[str, ...] = ()
    login_url_markers: tuple[str, ...] = ()
    block_status_codes: tuple[int, ...] = field(default=DEFAULT_BLOCK_STATUS_CODES)


def load_config(path: Path | None = None) -> CrawlerConfig:
    """Read the `crawler:` block. A missing contact is an error: the User-Agent must identify us."""
    path = path or Path(os.environ.get("REGCRAWLER_SETTINGS_FILE") or SETTINGS_FILE)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    block = data.get("crawler") or {}
    contact = str(block.get("contact") or "").strip()
    if not contact:
        raise ValueError(f"{path}: crawler.contact is required (it goes into the User-Agent)")
    codes = tuple(int(c) for c in block.get("block_status_codes") or ())
    return CrawlerConfig(
        contact=contact,
        block_markers=tuple(str(m).lower() for m in block.get("block_markers") or ()),
        login_url_markers=tuple(str(m).lower() for m in block.get("login_url_markers") or ()),
        # 401 always counts as "a login is required", whatever the file says.
        block_status_codes=tuple(sorted({401, *(codes or (403, 429))})),
    )
