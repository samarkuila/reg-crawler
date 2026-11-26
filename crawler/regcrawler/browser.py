"""Browser fetch engine helpers (fetch_engine = browser).

The browser only RENDERS the page. Parsing, snapshots, block detection and politeness are the same
as for the standard engine (CLAUDE.md rules 1 and 2): a plain headless Chromium, no stealth
plugins, no user-agent or fingerprint changes, no proxy rotation. The one proxy used is the
corporate proxy from the environment, the same one the standard engine uses.

A person chooses the engine per source; nothing here switches engines.
"""

import importlib.util
import os
import sys
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

ENGINE_ENV = "REGCRAWLER_FETCH_ENGINE"  # set by the launchers for a browser source's crawl only
INSTALL_HINT = "Run .\\tasks.ps1 install-browser, then try again."

WAIT_MISSING_HINT = "page rendered but list not found; check selectors or wait selector"


def scrapy_playwright_installed() -> bool:
    return importlib.util.find_spec("scrapy_playwright") is not None


def browsers_path() -> Path:
    """Where `playwright install chromium` puts the browser."""
    custom = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if custom and custom != "0":
        return Path(custom)
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "ms-playwright"
    return Path.home() / ".cache" / "ms-playwright"


def chromium_installed() -> bool:
    root = browsers_path()
    return root.is_dir() and any(p.name.startswith("chromium") for p in root.iterdir())


def unavailable_reason() -> str | None:
    """Why a browser-fetched source cannot be read now, or None when it can."""
    if not scrapy_playwright_installed():
        return (
            "This source is fetched with the browser, but scrapy-playwright is not installed. "
            + INSTALL_HINT
        )
    if not chromium_installed():
        return (
            "This source is fetched with the browser, but Chromium is not installed. "
            + INSTALL_HINT
        )
    return None


def proxy_option() -> dict[str, str] | None:
    """The corporate proxy from the environment, for Chromium (the standard engine reads the same
    variables). One fixed proxy: never rotated, never chosen per request."""
    value = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
    if not value:
        return None
    parts = urlsplit(value)
    if not parts.hostname:
        return None
    option = {"server": f"{parts.scheme or 'http'}://{parts.hostname}:{parts.port or 80}"}
    if parts.username:
        option["username"] = unquote(parts.username)
        option["password"] = unquote(parts.password or "")
    return option


async def wait_for_list(page: Any, selector: str, timeout_ms: int, outcome: dict[str, Any]) -> None:
    """Page method: wait for the element, and record whether it appeared.

    It never raises on a timeout: the rendered page is still returned (and snapshotted), block
    detection still judges it, and the spider decides what an absent list means (Zero items).
    """
    try:
        await page.wait_for_selector(selector, timeout=timeout_ms, state="attached")
    except Exception as exc:  # noqa: BLE001 - Playwright's TimeoutError is not importable here
        if type(exc).__name__ != "TimeoutError":
            raise
        outcome["found"] = False
    else:
        outcome["found"] = True
