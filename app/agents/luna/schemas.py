"""JSON schemas for Luna's answers (agent spec §4) and a small strict validator.

The schemas are sent to the provider as structured-output schemas, and the answer is checked against
them again here, because a provider setting is not a guarantee: unknown fields, wrong types and
values outside the allowed lists are rejected (an injected extra field such as "decision" is never
accepted).
"""

from typing import Any

RELEVANCE = ["HIGH", "MEDIUM", "LOW", "NONE"]
TREATMENTS = [
    "INFO_ONLY", "OTHER_FUNCTION", "FUTURE_POTENTIAL", "IMPLEMENTATION_REQUIRED", "NOT_RELEVANT",
]  # fmt: skip
SME_FUNCTIONS = ["Treasury", "Finance", "HR/Admin", "AML/Operations", "IT", "Legal", "None"]
URGENCY = ["URGENT", "NORMAL"]
EFFECTIVE_RULES = ["EXPLICIT", "IMMEDIATE_EFFECT", "DEFAULT_ISSUE_DATE"]
DOCUMENT_TYPES = [
    "Direction", "Master Direction", "Circular", "Notification", "FAQ", "Press release",
    "Gazette", "Sanctions change", "Other",
]  # fmt: skip


def _obj(props: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": props,
        "required": list(props),
        "additionalProperties": False,
    }


def _field(value_type: Any = "string", enum: list[str] | None = None) -> dict[str, Any]:
    value: dict[str, Any] = {"type": [value_type, "null"]}
    if enum:
        value["enum"] = [*enum, None]
    return _obj(
        {
            "value": value,
            "evidence_quote": {"type": ["string", "null"]},
            "location": {"type": ["string", "null"]},
            "confidence": {"type": ["number", "null"]},
        }
    )


SCREEN_SCHEMA = _obj(
    {
        "nbfc_relevance": {"type": "string", "enum": RELEVANCE},
        "relevance_score": {"type": "number"},
        "addressed_to": {"type": "array", "items": {"type": "string"}},
        "reason": {"type": "string"},
        "suggested_treatment": {"type": "string", "enum": TREATMENTS},
        "suggested_sme_function": {"type": "string", "enum": SME_FUNCTIONS},
        "urgency": {"type": "string", "enum": URGENCY},
        "evidence": {
            "type": "array",
            "items": _obj({"quote": {"type": "string"}, "location": {"type": "string"}}),
        },
    }
)

EXTRACT_FIELDS = (
    "ref_no", "issue_date", "publish_date", "effective_date", "effective_date_rule",
    "authority", "document_type", "impact_summary",
)  # fmt: skip
DATE_FIELDS = ("issue_date", "publish_date", "effective_date")

EXTRACT_SCHEMA = _obj(
    {
        "ref_no": _field(),
        "issue_date": _field(),
        "publish_date": _field(),
        "effective_date": _field(),
        "effective_date_rule": _field(enum=EFFECTIVE_RULES),
        "authority": _field(),
        "document_type": _field(enum=DOCUMENT_TYPES),
        "impact_summary": _field(),
    }
)

SWEEP_SCHEMA = _obj(
    {
        "possible_misses": {
            "type": "array",
            "items": _obj(
                {
                    "title": {"type": "string"},
                    "date_text": {"type": "string"},
                    "link_text": {"type": "string"},
                    "why_regulatory": {"type": "string"},
                    "evidence_quote": {"type": "string"},
                }
            ),
        }
    }
)


class SchemaError(ValueError):
    """The answer does not match the schema."""


def validate(data: Any, schema: dict[str, Any], path: str = "answer") -> None:
    """Strict check: types, required keys, no extra keys, enum values, array items."""
    types = schema.get("type")
    allowed = types if isinstance(types, list) else [types]
    if not any(_is(data, t) for t in allowed):
        raise SchemaError(f"{path}: expected {allowed}, got {type(data).__name__}")
    if "enum" in schema and data not in schema["enum"]:
        raise SchemaError(f"{path}: {data!r} is not an allowed value")
    if isinstance(data, dict) and "properties" in schema:
        extra = set(data) - set(schema["properties"])
        if extra:
            raise SchemaError(f"{path}: unexpected fields {sorted(extra)}")
        for key in schema.get("required", []):
            if key not in data:
                raise SchemaError(f"{path}: missing {key}")
        for key, sub in schema["properties"].items():
            validate(data[key], sub, f"{path}.{key}")
    if isinstance(data, list) and "items" in schema:
        for i, item in enumerate(data):
            validate(item, schema["items"], f"{path}[{i}]")


def _is(value: Any, kind: str | None) -> bool:
    if kind == "string":
        return isinstance(value, str)
    if kind == "number":
        return isinstance(value, int | float) and not isinstance(value, bool)
    if kind == "object":
        return isinstance(value, dict)
    if kind == "array":
        return isinstance(value, list)
    if kind == "null":
        return value is None
    return False
