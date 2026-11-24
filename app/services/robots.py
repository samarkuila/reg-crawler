"""robots.txt pre-check: read a website's rules once per run, before any page is requested.

The crawler (Scrapy) obeys robots.txt too; this check makes the answer visible *before* a process
is started: a source the website does not allow us to read is recorded as BLOCKED and skipped.
Nothing is worked around (CLAUDE.md rule 1). Plain GET with our identifying User-Agent, no retries.
"""

import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

TIMEOUT_SECONDS = 15
SETTINGS_FILE = Path(__file__).resolve().parents[2] / "config" / "settings.yaml"


def default_user_agent() -> str:
    """The same identifying User-Agent the crawler sends (contact comes from settings.yaml)."""
    import yaml

    try:
        data = yaml.safe_load(SETTINGS_FILE.read_text(encoding="utf-8")) or {}
        contact = str((data.get("crawler") or {}).get("contact") or "")
    except (OSError, yaml.YAMLError):
        contact = ""
    return f"TVSCredit-RegMonitor/0.1 (+{contact})"


def make_robots_check() -> "RobotsCheck":
    return RobotsCheck(default_user_agent())


# fetch(url) -> (status code, body text); raises OSError when the site cannot be reached
Fetch = Callable[[str], tuple[int, str]]


def _http_fetch(user_agent: str) -> Fetch:
    def fetch(url: str) -> tuple[int, str]:
        request = urllib.request.Request(url, headers={"User-Agent": user_agent})
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as resp:  # noqa: S310
                return resp.status, resp.read(512_000).decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            return exc.code, ""

    return fetch


class RobotsCheck:
    """Allowed / not allowed for one source url. One robots.txt download per website per run."""

    def __init__(self, user_agent: str, fetch: Fetch | None = None) -> None:
        self._agent = user_agent
        self._fetch = fetch or _http_fetch(user_agent)
        self._rules: dict[str, RobotFileParser | str | None] = {}

    def blocked_reason(self, url: str) -> str | None:
        """Plain-language reason when robots.txt does not allow `url`; None when allowed or
        unknown (a missing file or an unreachable site is left to the crawler to report)."""
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return None
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin not in self._rules:
            self._rules[origin] = self._load(origin)
        rules = self._rules[origin]
        if isinstance(rules, str):  # the whole site is refused (401/403 on robots.txt)
            return rules
        if rules is not None and not rules.can_fetch(self._agent, url):
            return (
                f"robots.txt on {parts.netloc} does not allow automated reading of this page. "
                "Nothing was requested or bypassed; check the page by hand"
            )
        return None

    def _load(self, origin: str) -> RobotFileParser | str | None:
        try:
            status, body = self._fetch(f"{origin}/robots.txt")
        except (OSError, ValueError):
            return None
        if status in (401, 403):
            return (
                f"robots.txt on {urlparse(origin).netloc} refuses automated access "
                f"(HTTP {status}). "
                "Nothing was requested or bypassed; check the page by hand"
            )
        if status != 200:
            return None  # no robots.txt (404) or a server problem: no rules to apply
        parser = RobotFileParser()
        parser.parse(body.splitlines())
        return parser
