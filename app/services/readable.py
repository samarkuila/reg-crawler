"""Plain-language views of stored values: labels, before/after differences, key/value lists.

Used by the Sources, Settings and Audit pages so people read "Ministry filter: Finance, MSME"
instead of JSON. Presentation helpers only: nothing here decides or changes anything.
"""

from typing import Any

from app.schemas.api import FieldChange, KeyValue

LABELS = {
    "authority": "Authority",
    "page_name": "Page name",
    "type": "How we read it",
    "url": "Page URL",
    "rss_url": "RSS URL",
    "priority": "Priority",
    "requires_js": "Needs a browser to render",
    "active": "Active",
    "include_keywords": "Include keywords",
    "exclude_keywords": "Exclude keywords",
    "adapter_config": "Reading rules",
    "adapter_config.tabs": "Tabs",
    "adapter_config.ministries": "Ministry filter",
    "adapter_config.date_format": "Date format",
    "adapter_config.selectors.row": "Row selector",
    "adapter_config.selectors.title": "Title selector",
    "adapter_config.selectors.link": "Link selector",
    "adapter_config.selectors.date": "Date selector",
    "adapter_config.selectors.ref_no_regex": "Reference no. pattern",
    "morning": "Morning run",
    "eod": "End-of-day run",
    "urgent_hourly_from": "Hourly urgent checks from",
    "urgent_hourly_to": "Hourly urgent checks until",
    "subject_pattern": "Subject pattern",
    "greeting": "Greeting",
    "intro": "Introduction",
    "disclaimer": "Disclaimer",
    "no_update_text": "No-update sentence",
    "sign_off": "Sign-off",
    "sender_name": "Sender name",
    "head_name": "Head of function",
    "head_email": "Head's email",
    "kind": "To or CC",
    "email": "Email",
    "name": "Name",
}


def label(path: str) -> str:
    if path in LABELS:
        return LABELS[path]
    last = path.rsplit(".", 1)[-1]
    return LABELS.get(last) or last.replace("_", " ").capitalize()


def text(value: Any) -> str:
    """One short readable string for any stored value."""
    if value is None or value == "":
        return "–"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, list):
        return ", ".join(text(v) for v in value) if value else "–"
    if isinstance(value, dict):
        return "; ".join(f"{label(k)}: {text(v)}" for k, v in value.items()) or "–"
    return str(value)


def _flat(value: Any, path: str = "") -> dict[str, Any]:
    """Nested dicts become dotted paths; lists of plain values stay together."""
    if isinstance(value, dict) and value:
        out: dict[str, Any] = {}
        for k, v in value.items():
            out.update(_flat(v, f"{path}.{k}" if path else str(k)))
        return out
    return {path: value}


def diff_fields(before: Any, after: Any, prefix: str = "") -> list[FieldChange]:
    """Only what changed, one row per field, in plain language."""
    b = _flat(before if isinstance(before, dict) else {"value": before}, prefix)
    a = _flat(after if isinstance(after, dict) else {"value": after}, prefix)
    rows = []
    for key in [*a, *(k for k in b if k not in a)]:
        if b.get(key) != a.get(key):
            rows.append(
                FieldChange(label=label(key), before=text(b.get(key)), after=text(a.get(key)))
            )
    return rows


def diff_lists(before: list[Any], after: list[Any]) -> list[FieldChange]:
    """A list of items (SME functions, recipients): name what was added, removed or edited."""
    key = "id" if all(isinstance(x, dict) and "id" in x for x in [*before, *after]) else None
    if key is None:
        b = [text(x) for x in before]
        a = [text(x) for x in after]
        if b == a:
            return []
        return [FieldChange(label="List", before=", ".join(b) or "–", after=", ".join(a) or "–")]
    old = {x["id"]: x for x in before}
    new = {x["id"]: x for x in after}
    rows: list[FieldChange] = []
    for i, item in new.items():
        name = str(item.get("name", i))
        if i not in old:
            rows.append(FieldChange(label=f"Added: {name}", before="–", after=text(item)))
            continue
        for f in diff_fields(old[i], item):
            rows.append(FieldChange(label=f"{name} · {f.label}", before=f.before, after=f.after))
    for i, item in old.items():
        if i not in new:
            rows.append(
                FieldChange(label=f"Removed: {item.get('name', i)}", before=text(item), after="–")
            )
    return rows


def key_values(value: Any) -> list[KeyValue]:
    """An audit event's before/after as readable rows."""
    if value is None:
        return []
    if not isinstance(value, dict):
        return [KeyValue(key="Value", value=text(value))]
    return [KeyValue(key=label(k), value=text(v)) for k, v in _flat(value).items()]
