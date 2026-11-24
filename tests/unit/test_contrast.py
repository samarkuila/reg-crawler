"""WCAG AA contrast for every text/background pair defined by the design tokens."""

import re
from pathlib import Path

import pytest

TOKENS = Path(__file__).resolve().parents[2] / "app" / "static" / "css" / "tokens.css"


def _vars() -> dict[str, str]:
    return dict(re.findall(r"(--[a-z0-9-]+):\s*(#[0-9A-Fa-f]{6})", TOKENS.read_text("utf-8")))


def _lum(hex_colour: str) -> float:
    rgb = [int(hex_colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
    lin = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in rgb]
    return 0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]


def _ratio(a: str, b: str) -> float:
    hi, lo = sorted((_lum(a), _lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


TEXT_PAIRS = [
    ("--c-on-brand", "--c-blue"),
    ("--c-on-brand", "--c-green-strong"),
    ("--c-header-text-soft", "--c-blue"),
    ("--c-grey-600", "--c-bg"),
    ("--c-grey-600", "--c-surface"),
    ("--c-grey-600", "--c-grey-25"),
    ("--c-grey-900", "--c-bg"),
    ("--c-grey-800", "--c-surface"),
    ("--c-grey-700", "--c-bg"),
    ("--c-blue", "--c-surface"),
    ("--c-blue", "--c-blue-tint"),
    ("--c-blue", "--c-blue-wash"),
    ("--c-blue-hover", "--c-surface"),
    ("--c-blue-hover", "--c-grey-50"),
    ("--c-danger", "--c-surface"),
    ("--c-danger", "--c-danger-bg"),
    ("--c-danger-ink", "--c-danger-bg"),
    ("--b-new-fg", "--b-new-bg"),
    ("--b-mod-fg", "--b-mod-bg"),
    ("--b-back-fg", "--b-back-bg"),
    ("--b-urg-fg", "--b-urg-bg"),
    ("--b-ok-fg", "--b-ok-bg"),
    ("--b-grey-fg", "--b-grey-bg"),
]


@pytest.mark.parametrize(("fg", "bg"), TEXT_PAIRS)
def test_text_contrast_aa(fg: str, bg: str) -> None:
    v = _vars()
    assert _ratio(v[fg], v[bg]) >= 4.5, f"{fg} on {bg}"


@pytest.mark.parametrize(
    "token",
    ["--st-not-applicable", "--st-action-over", "--st-in-progress", "--st-not-as-per-plan"],
)
def test_status_dot_contrast_on_white(token: str) -> None:
    # Non-text graphics need 3:1 (WCAG 1.4.11). "Yet to start" yellow sits inside a
    # bordered pill with a text label, so the label, not the dot, carries the meaning.
    v = _vars()
    assert _ratio(v[token], v["--c-surface"]) >= 3.0, token
