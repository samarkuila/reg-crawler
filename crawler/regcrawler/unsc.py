"""UNSC consolidated list: read the XML and compare two versions of it.

Pure functions, no network and no database. The spider downloads and stores the file; this module
turns two stored files into ADDED / REMOVED / AMENDED changes (reference number, names, aliases).
It reports what changed in the data. It does not decide whether TVS Credit is affected.

The file is the UN Security Council Consolidated List: <INDIVIDUALS>/<INDIVIDUAL> and
<ENTITIES>/<ENTITY>, each with REFERENCE_NUMBER (for example QDi.001), name parts, repeated
<..._ALIAS><ALIAS_NAME> and other detail elements (dates, places, addresses, comments).
"""

import re
from dataclasses import dataclass, field
from enum import StrEnum

from defusedxml import ElementTree as SafeET  # no entity expansion from a downloaded file

_SPACE = re.compile(r"\s+")
# Name parts, in reading order. Anything else under an entry is compared as a "detail".
_NAME_TAGS = ("FIRST_NAME", "SECOND_NAME", "THIRD_NAME", "FOURTH_NAME")
_HANDLED = {*_NAME_TAGS, "REFERENCE_NUMBER", "INDIVIDUAL_ALIAS", "ENTITY_ALIAS"}
_KINDS = {"INDIVIDUAL": "individual", "ENTITY": "entity"}


class UnscError(ValueError):
    """The file is not a readable consolidated list."""


class Change(StrEnum):
    ADDED = "ADDED"
    REMOVED = "REMOVED"
    AMENDED = "AMENDED"


@dataclass(frozen=True)
class Entry:
    ref: str
    kind: str  # "individual" | "entity"
    name: str
    aliases: tuple[str, ...]  # as written, sorted
    listed_on: str | None
    details: tuple[tuple[str, tuple[str, ...]], ...]  # (element name, sorted values)


@dataclass(frozen=True)
class Difference:
    change: Change
    ref: str
    kind: str
    name: str
    summary: str
    aliases: tuple[str, ...] = ()
    aliases_added: tuple[str, ...] = ()
    aliases_removed: tuple[str, ...] = ()
    name_before: str | None = None
    changed_details: tuple[str, ...] = ()
    listed_on: str | None = None


@dataclass
class Listing:
    entries: dict[str, Entry] = field(default_factory=dict)
    skipped: int = 0  # entries with neither a reference number nor a data id
    generated: str | None = None  # dateGenerated attribute of the root element


def _text(element) -> str:  # type: ignore[no-untyped-def]
    return _SPACE.sub(" ", "".join(element.itertext())).strip()


def _fold(value: str) -> str:
    return _SPACE.sub(" ", value).strip().casefold()


def parse_consolidated(content: bytes) -> Listing:
    """Entries by reference number. Raises UnscError if this is not a consolidated list."""
    try:
        root = SafeET.fromstring(content)
    except Exception as exc:  # malformed XML, forbidden entities, empty file, an HTML page
        raise UnscError(f"Not readable XML: {exc}") from exc
    if root.tag.upper() != "CONSOLIDATED_LIST":
        raise UnscError(f"Not a UN consolidated list (root element is <{root.tag}>)")
    listing = Listing(generated=root.get("dateGenerated"))
    for element in root.iter():
        kind = _KINDS.get(element.tag.upper())
        if kind is None:
            continue
        entry = _entry(element, kind)
        if entry is None:
            listing.skipped += 1
        elif entry.ref in listing.entries:
            listing.skipped += 1  # a repeated reference: keep the first, never overwrite
        else:
            listing.entries[entry.ref] = entry
    return listing


def _entry(element, kind: str) -> Entry | None:  # type: ignore[no-untyped-def]
    values: dict[str, list[str]] = {}
    aliases: list[str] = []
    name_parts: list[str] = []
    ref = ""
    for child in element:
        tag = child.tag.upper()
        if tag == "REFERENCE_NUMBER":
            ref = _text(child)
        elif tag in _NAME_TAGS:
            if _text(child):
                name_parts.append(_text(child))
        elif tag in ("INDIVIDUAL_ALIAS", "ENTITY_ALIAS"):
            alias = next((_text(c) for c in child if c.tag.upper() == "ALIAS_NAME"), "")
            if alias:
                aliases.append(alias)
        elif tag not in _HANDLED:
            text = _text(child)
            if text:
                values.setdefault(tag, []).append(text)
    if not ref:
        data_id = next((_text(c) for c in element if c.tag.upper() == "DATAID"), "")
        if not data_id:
            return None
        ref = f"DATAID-{data_id}"
    listed_on = next((v[0] for t, v in values.items() if t == "LISTED_ON"), None)
    return Entry(
        ref=ref,
        kind=kind,
        name=" ".join(name_parts),
        aliases=tuple(sorted(aliases, key=_fold)),
        listed_on=listed_on,
        details=tuple((tag, tuple(sorted(v))) for tag, v in sorted(values.items())),
    )


def _signature(entry: Entry) -> tuple:  # type: ignore[type-arg]
    """What counts as "the same entry": name and aliases ignoring case and spacing."""
    return (
        _fold(entry.name),
        tuple(sorted(_fold(a) for a in entry.aliases)),
        entry.details,
    )


def _label(tag: str) -> str:
    return tag.lower().replace("_", " ")


def _amended(old: Entry, new: Entry) -> Difference:
    old_alias = {_fold(a): a for a in old.aliases}
    new_alias = {_fold(a): a for a in new.aliases}
    added = tuple(new_alias[k] for k in new_alias if k not in old_alias)
    removed = tuple(old_alias[k] for k in old_alias if k not in new_alias)
    old_details, new_details = dict(old.details), dict(new.details)
    changed = tuple(
        _label(tag)
        for tag in sorted({*old_details, *new_details})
        if old_details.get(tag) != new_details.get(tag)
    )
    parts: list[str] = []
    if added:
        parts.append(f"{'alias' if len(added) == 1 else 'aliases'} added: {', '.join(added)}")
    if removed:
        parts.append(f"{'alias' if len(removed) == 1 else 'aliases'} removed: {', '.join(removed)}")
    if _fold(old.name) != _fold(new.name):
        parts.append(f"name changed from {old.name}")
    if changed:
        parts.append(f"details changed: {', '.join(changed)}")
    return Difference(
        change=Change.AMENDED,
        ref=new.ref,
        kind=new.kind,
        name=new.name,
        summary="; ".join(parts) or "entry updated",
        aliases=new.aliases,
        aliases_added=added,
        aliases_removed=removed,
        name_before=old.name if _fold(old.name) != _fold(new.name) else None,
        changed_details=changed,
        listed_on=new.listed_on,
    )


def diff(old: Listing, new: Listing) -> list[Difference]:
    """What changed from `old` to `new`: ADDED, then AMENDED, then REMOVED, each by reference."""
    out: list[Difference] = []
    for ref in sorted(new.entries.keys() - old.entries.keys()):
        e = new.entries[ref]
        out.append(
            Difference(
                Change.ADDED,
                ref,
                e.kind,
                e.name,
                "new entry",
                aliases=e.aliases,
                listed_on=e.listed_on,
            )
        )
    for ref in sorted(new.entries.keys() & old.entries.keys()):
        if _signature(new.entries[ref]) != _signature(old.entries[ref]):
            out.append(_amended(old.entries[ref], new.entries[ref]))
    for ref in sorted(old.entries.keys() - new.entries.keys()):
        e = old.entries[ref]
        out.append(
            Difference(
                Change.REMOVED,
                ref,
                e.kind,
                e.name,
                "removed from the list",
                aliases=e.aliases,
                listed_on=e.listed_on,
            )
        )
    order = {Change.ADDED: 0, Change.AMENDED: 1, Change.REMOVED: 2}
    return sorted(out, key=lambda d: (order[d.change], d.ref))
