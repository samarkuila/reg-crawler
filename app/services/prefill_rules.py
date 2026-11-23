"""Rules that propose values for the review form (FR-34, FR-35). Suggestions only.

Pure functions over the document text and the Entity Profile. They work with the Luna agent off;
when it is on, it is a second opinion and these rules are still shown. A reviewer always decides.
"""

import re
from dataclasses import dataclass
from datetime import date

from app.schemas.enums import Applicability

# --- FR-34: effective date -----------------------------------------------------------------------

_MONTHS = {
    m: i
    for i, names in enumerate(
        [
            ("january", "jan"),
            ("february", "feb"),
            ("march", "mar"),
            ("april", "apr"),
            ("may",),
            ("june", "jun"),
            ("july", "jul"),
            ("august", "aug"),
            ("september", "sep", "sept"),
            ("october", "oct"),
            ("november", "nov"),
            ("december", "dec"),
        ],
        start=1,
    )
    for m in names
}
_MONTH = "|".join(sorted(_MONTHS, key=len, reverse=True))
_DAY = r"(\d{1,2})(?:st|nd|rd|th)?"
_DATE_FORMS = [
    # 01.12.2026  01-12-2026  01/12/2026 (day first, as Indian regulators write)
    (re.compile(r"(\d{1,2})[./-](\d{1,2})[./-](\d{4})"), "dmy_numeric"),
    # 1st December 2026 / 1 Dec, 2026
    (re.compile(rf"{_DAY}\s+(?:of\s+)?({_MONTH})\.?,?\s+(\d{{4}})", re.I), "dmy_text"),
    # December 1, 2026
    (re.compile(rf"({_MONTH})\.?\s+{_DAY},?\s+(\d{{4}})", re.I), "mdy_text"),
    # 2026-12-01
    (re.compile(r"(\d{4})-(\d{2})-(\d{2})"), "iso"),
]
_TRIGGER = re.compile(
    r"(with\s+effect\s+from|w\.?e\.?f\.?|effective\s+(?:from|on|date)|"
    r"(?:shall\s+)?(?:come|comes)\s+into\s+(?:force|effect)\s+(?:on|from)|"
    r"(?:shall\s+)?be\s+effective\s+(?:from|on)|(?:shall\s+)?apply\s+from|"
    r"(?:is|are|shall\s+be)\s+effective\s+from)\s*[:,]?\s*",
    re.I,
)
_IMMEDIATE = re.compile(
    r"(with\s+immediate\s+effect|effective\s+immediately|"
    r"(?:come|comes|came)\s+into\s+(?:force|effect)\s+immediately|immediately\s+effective)",
    re.I,
)
_WINDOW = 60  # characters after the trigger phrase in which the date must start


def _parse_dates(text: str) -> list[tuple[int, date, str]]:
    found: list[tuple[int, date, str]] = []
    for pattern, kind in _DATE_FORMS:
        for m in pattern.finditer(text):
            try:
                if kind == "dmy_numeric":
                    value = date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
                elif kind == "dmy_text":
                    value = date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
                elif kind == "mdy_text":
                    value = date(int(m.group(3)), _MONTHS[m.group(1).lower()], int(m.group(2)))
                else:
                    value = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            except (ValueError, KeyError):
                continue
            found.append((m.start(), value, m.group(0)))
    return sorted(found)


def _squash(text: str) -> str:
    return re.sub(r"\s+", " ", text or "")


@dataclass(frozen=True)
class EffectiveDate:
    value: date | None
    rule: str  # plain English: which rule gave this date (shown to the reviewer, FR-34)


def effective_date(
    text: str | None,
    issue: date | None,
    publish: date | None,
    authority: str | None = None,
) -> EffectiveDate:
    """Effective date by the FR-34 rules, in this order:

    1. an explicit date after a phrase like "with effect from" -> that date;
    2. "with immediate effect" / "effective immediately" -> the issue date;
    3. nothing stated: UNSC/UN notices -> the publish date; others -> the issue date, else the
       publish date.
    """
    body = _squash(text)
    dates = _parse_dates(body)
    for trigger in _TRIGGER.finditer(body):
        for position, value, shown in dates:
            if trigger.end() <= position <= trigger.end() + _WINDOW:
                quote = body[trigger.start() : position + len(shown)]
                return EffectiveDate(value, f"Date stated in the text: “{quote}”")
    immediate = _IMMEDIATE.search(body)
    if immediate:
        return EffectiveDate(
            issue or publish,
            f"The text says “{immediate.group(0)}”: using the issue date",
        )
    if (authority or "").upper() in ("UNSC", "UN"):
        return EffectiveDate(
            publish, "UN notice: using the publish date unless the text says otherwise"
        )
    if issue:
        return EffectiveDate(issue, "No effective date found: using the issue date")
    return EffectiveDate(publish, "No effective date found: using the publish date")


# --- FR-35: applicability hint -------------------------------------------------------------------


@dataclass(frozen=True)
class ApplicabilityHint:
    suggested: Applicability
    text: str  # for example "Not applicable — not a system participant"
    phrase: str  # the words in the document this is based on
    profile_item: str


_STOP = {
    "a", "an", "the", "of", "to", "in", "with", "under", "and", "or", "for", "by", "registered",
    "entity", "entities", "holder", "holders", "rbi", "all", "any", "such", "which", "that",
    "are", "is", "be", "as", "on", "at",
}  # fmt: skip
# Extra ways a document names the same kind of entity as a profile item (the profile can add
# its own as "aliases").
_BUILTIN_ALIASES = {
    "nbfc registered with rbi": ["nbfc", "nbfcs", "non-banking financial compan", "nbfc-icc"],
    "payment system participant": [
        "system participant", "payment system operator", "payment system provider",
        "authorised payment system", "pso",
    ],
    "insurance corporate agent": ["corporate agent", "insurance agent", "insurance intermediar"],
    "fema authorised dealer": ["authorised dealer", "authorized dealer", "ad category"],
    "reporting entity under pmla": ["reporting entit", "pmla"],
}  # fmt: skip
_APPLICABLE = re.compile(
    r"(?:applicable|applies|apply|applicability|addressed|directed|issued)\s+to\s+"
    r"(?:all\s+|the\s+|every\s+|each\s+)?([^.;:\n]{3,140})",
    re.I,
)


def _tokens(text: str) -> set[str]:
    return {
        t[:-1] if t.endswith("s") and len(t) > 3 else t
        for t in re.findall(r"[a-z][a-z-]+", text.lower())
        if t not in _STOP
    }


def _a(name: str) -> str:
    """'Payment system participant' -> 'a payment system participant'; 'NBFC x' -> 'an NBFC x'."""
    name = name.strip().rstrip(".")
    first = name.split(" ", 1)[0]
    if len(first) > 1 and first.isupper():  # an acronym keeps its capitals
        article = "an" if first[0] in "AEFHILMNORSX" else "a"
        return f"{article} {name}"
    name = name[:1].lower() + name[1:]
    return f"{'an' if name[:1] in 'aeiou' else 'a'} {name}"


def applicability_hint(text: str | None, profile: dict | None) -> ApplicabilityHint | None:  # type: ignore[type-arg]
    """Suggest an applicability by matching "applicable to …" phrases against the Entity Profile.

    Only a suggestion. If the profile is empty or does not settle the question, the suggestion is
    "To be confirmed" and says why. Returns None when the document has no such phrase.
    """
    items = (profile or {}).get("items") or []
    body = _squash(text)
    if not items or not body:
        return None
    for match in _APPLICABLE.finditer(body):
        phrase = match.group(1).strip()
        words = _tokens(phrase)
        low = phrase.lower()
        for item in items:
            name = str(item.get("name") or "")
            aliases = [a.lower() for a in item.get("aliases") or []]
            aliases += _BUILTIN_ALIASES.get(name.lower(), [])
            name_words = _tokens(name)
            hit = any(a in low for a in aliases) or (len(name_words) >= 2 and name_words <= words)
            if not hit:
                continue
            held = item.get("held")
            quote = f"applicable to {phrase}"
            if held is True:
                return ApplicabilityHint(
                    Applicability.YES,
                    f"Applicable — TVS Credit is {_a(name)}",
                    quote,
                    name,
                )
            if held is False:
                return ApplicabilityHint(
                    Applicability.NO,
                    f"Not applicable — not {_a(name)}",
                    quote,
                    name,
                )
            return ApplicabilityHint(
                Applicability.TBC,
                f"To be confirmed — the Entity Profile does not say whether TVS Credit is "
                f"{_a(name)}",
                quote,
                name,
            )
    return None
