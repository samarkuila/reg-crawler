"""Small shared formatting helpers (dates are DD.MM.YYYY everywhere users see them)."""

from datetime import date, datetime

from app.schemas.enums import Cycle

CYCLE_NAMES = {
    Cycle.MORNING: "Morning",
    Cycle.EOD: "EOD",
    Cycle.ADHOC: "Ad hoc",
    Cycle.URGENT: "Urgent",
}


def fmt_date(d: date | None, empty: str = "–") -> str:
    return d.strftime("%d.%m.%Y") if d else empty


def fmt_time(dt: datetime) -> str:
    return dt.strftime("%H:%M")
