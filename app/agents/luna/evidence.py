"""Evidence validation (policy A3, spec §5.3): every quote must be in the text we sent.

A quote counts only if, after collapsing whitespace, it appears word for word in the input text
(case and punctuation must match). Otherwise the field it supports is set to null.
"""

import re

_SPACE = re.compile(r"\s+")
MIN_QUOTE = 3  # a quote shorter than this proves nothing


def normalise(text: str | None) -> str:
    return _SPACE.sub(" ", (text or "").replace("\xa0", " ")).strip()


def quote_is_verbatim(quote: str | None, text: str) -> bool:
    """True if `quote` appears exactly in `text` (whitespace-normalised)."""
    q = normalise(quote)
    return len(q) >= MIN_QUOTE and q in normalise(text)


class Haystack:
    """The text a request carried, normalised once, for checking many quotes."""

    def __init__(self, *parts: str) -> None:
        self._text = normalise("\n".join(parts))

    def has(self, quote: str | None) -> bool:
        q = normalise(quote)
        return len(q) >= MIN_QUOTE and q in self._text
