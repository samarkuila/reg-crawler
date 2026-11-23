"""Keyword relevance (FR-16): a score and a plain-English reason. Ranking only, never hiding.

Include keywords (global setting + the source's own) raise the score, exclude keywords lower it.
Nothing here can remove an item: a low score only means the review page collapses the item
(CLAUDE.md rule 3). The Luna agent, when on, is a second opinion; this rule score is always there.

The score is between 0 and 1; the item stores it as a whole number 0-100.
"""

import re
from dataclasses import dataclass

NEUTRAL = 0.5
EXCLUDE_FACTOR = 0.4  # each excluded word multiplies the score by this
MAX_TEXT = 4000  # only the start of a document is read: titles and openings carry the subject


@dataclass(frozen=True)
class Relevance:
    score: float  # 0..1
    include_hits: tuple[str, ...]
    exclude_hits: tuple[str, ...]
    reason: str

    @property
    def percent(self) -> int:
        return round(self.score * 100)


def _pattern(keyword: str) -> re.Pattern[str]:
    # Whole words or phrases, any case: "KYC" must not match "KYCs of ..." by accident of
    # spelling, and "bid" must not match "forbidden".
    return re.compile(rf"(?<![\w]){re.escape(keyword.strip())}(?![\w])", re.IGNORECASE)


def _hits(text: str, keywords: list[str]) -> tuple[str, ...]:
    found: list[str] = []
    for keyword in keywords:
        if keyword and keyword.strip() and _pattern(keyword).search(text):
            if keyword.strip().casefold() not in (f.casefold() for f in found):
                found.append(keyword.strip())
    return tuple(found)


def _join(words: tuple[str, ...]) -> str:
    quoted = [f"'{w}'" for w in words]
    return quoted[0] if len(quoted) == 1 else ", ".join(quoted[:-1]) + " and " + quoted[-1]


def score(
    title: str,
    include: list[str],
    exclude: list[str],
    text: str | None = None,
) -> Relevance:
    """Score one item from its title (and the start of its text, if there is any)."""
    haystack = f"{title}\n{(text or '')[:MAX_TEXT]}"
    inc, exc = _hits(haystack, include), _hits(haystack, exclude)
    value = NEUTRAL
    if inc:
        value = NEUTRAL + (1 - NEUTRAL) * (1 - 0.5 ** len(inc))  # 1 hit .75, 2 hits .875 ...
    if exc:
        value *= EXCLUDE_FACTOR ** len(exc)
    value = round(min(max(value, 0.0), 1.0), 3)
    if inc and exc:
        reason = (
            f"Mentions {_join(inc)} (relevant to an NBFC) but also {_join(exc)} "
            "(usually not relevant); ranked in the middle."
        )
    elif inc:
        reason = f"Mentions {_join(inc)}; ranked higher."
    elif exc:
        reason = f"Mentions {_join(exc)}, which is usually not relevant to an NBFC; ranked lower."
    else:
        reason = "No keyword matched, so the rank is neutral. Please read it."
    return Relevance(value, inc, exc, reason)
