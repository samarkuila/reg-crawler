"""Column types that behave the same on SQLite and PostgreSQL (no database-specific features)."""

import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy import Enum as SAEnum
from sqlalchemy.types import TypeDecorator


def new_id() -> str:
    """Primary keys are UUID strings (36 characters), portable to every database."""
    return str(uuid.uuid4())


class TZDateTime(TypeDecorator[datetime]):
    """Timezone-aware datetimes, stored as UTC.

    SQLite has no timezone type, so a plain DateTime comes back naive. This converts to UTC on
    the way in and attaches UTC on the way out, so both databases return the same instant.
    Naive values are refused: a time without a zone is ambiguous in an audit trail.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("Datetimes must carry a timezone (use an aware datetime)")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def enum_column(cls: type[Enum], name: str) -> SAEnum:
    """A text column limited to the enum's values by a CHECK constraint (no native ENUM type)."""
    return SAEnum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        validate_strings=True,
        length=max(len(m.value) for m in cls) + 4,
        values_callable=lambda e: [m.value for m in e],
    )
