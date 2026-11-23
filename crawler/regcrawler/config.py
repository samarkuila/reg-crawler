"""Crawler configuration read from config/settings.yaml (config is data, not code)."""

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = ROOT / "data"
SETTINGS_FILE = ROOT / "config" / "settings.yaml"

DEFAULT_BLOCK_STATUS_CODES = (401, 403, 429)
# Built in, so a shorter list in the file can never switch block detection off: the file can only
# ADD markers (CLAUDE.md rule 1: a block is always recorded, never worked around).
DEFAULT_BLOCK_MARKERS = (
    "captcha", "g-recaptcha", "h-captcha", "cf-challenge", "just a moment...",
    "checking your browser", "attention required", "access denied", "request unsuccessful",
    "request blocked", "are you a robot", "unusual traffic", "verify you are human",
    "bot detection", "enable javascript and cookies to continue",
)  # fmt: skip
DEFAULT_LOGIN_MARKERS = ("/login", "/signin", "/sign-in", "/sso/", "/auth/", "/account/login")
PLACEHOLDER_CONTACT = ("todo_", "example.invalid")


@dataclass(frozen=True)
class CrawlerConfig:
    contact: str
    block_markers: tuple[str, ...] = ()
    login_url_markers: tuple[str, ...] = ()
    block_status_codes: tuple[int, ...] = field(default=DEFAULT_BLOCK_STATUS_CODES)

    @property
    def contact_is_placeholder(self) -> bool:
        """True while the contact is still a TODO_ or example.invalid value (no live crawl yet)."""
        return any(token in self.contact.lower() for token in PLACEHOLDER_CONTACT)


def _merged(defaults: tuple[str, ...], configured: object) -> tuple[str, ...]:
    extra = [str(m).lower() for m in (configured or ())]  # type: ignore[attr-defined]
    return tuple(dict.fromkeys([*defaults, *extra]))


def load_config(path: Path | None = None) -> CrawlerConfig:
    """Read the `crawler:` block. A missing contact is an error: the User-Agent must identify us.

    The contact is `crawler.contact` (or `crawler.contact_email`). Block and login markers from the
    file are added to the built-in ones.
    """
    path = path or Path(os.environ.get("REGCRAWLER_SETTINGS_FILE") or SETTINGS_FILE)
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    block = data.get("crawler") or {}
    contact = str(block.get("contact") or block.get("contact_email") or "").strip()
    if not contact:
        raise ValueError(f"{path}: crawler.contact is required (it goes into the User-Agent)")
    codes = tuple(int(c) for c in block.get("block_status_codes") or ())
    return CrawlerConfig(
        contact=contact,
        block_markers=_merged(DEFAULT_BLOCK_MARKERS, block.get("block_markers")),
        login_url_markers=_merged(DEFAULT_LOGIN_MARKERS, block.get("login_url_markers")),
        # 401 always counts as "a login is required", whatever the file says.
        block_status_codes=tuple(sorted({401, *(codes or (403, 429))})),
    )
