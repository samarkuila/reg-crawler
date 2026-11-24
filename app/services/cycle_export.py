"""FR-32: export every captured item of a cycle (low relevance and dismissed included) to CSV.

CSV is written with a UTF-8 byte-order mark so Excel opens it with the right characters. The last
column, "Compliance marking", is left blank for Compliance to fill in (it feeds Phase 2 filters).
Nothing is filtered out: this is the complete list of what the system captured.
"""

import csv
import io

from app.repositories.base import Repository
from app.schemas.api import CycleRef, ItemFilter
from app.schemas.entities import AppUser, ReviewDecision
from app.schemas.enums import Treatment
from app.services.errors import InvalidError, NotFoundError
from app.services.formatting import fmt_date
from app.services.permissions import REVIEW_ROLES, require_role
from app.services.review import ReviewService, latest_final_decision

HEADERS = [
    "Internal ref",
    "Authority",
    "Page",
    "Title",
    "Reference no.",
    "Date shown on website",
    "First seen",
    "Channel",
    "Change type",
    "Relevance",
    "Relevance score",
    "Item state",
    "Review outcome",
    "Reason for dismissing",
    "Treatment",
    "In daily update",
    "Link",
    "Compliance marking",
]


def _outcome(decision: ReviewDecision | None) -> str:
    if decision is None:
        return "No decision yet"
    if decision.treatment == Treatment.NOT_RELEVANT:
        return "Dismissed"
    return "Accepted"


def _safe(text: str) -> str:
    """Stop a spreadsheet reading a cell as a formula (text from websites is untrusted)."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


class CycleExportService:
    def __init__(self, repo: Repository, review: ReviewService) -> None:
        self._repo = repo
        self._review = review

    def export_csv(self, user: AppUser, cycle: str | None) -> tuple[bytes, str]:
        """Return (file bytes, file name) for the cycle. Defaults to the latest cycle."""
        require_role(user, REVIEW_ROLES, "export the cycle")
        try:
            ref = CycleRef.parse(cycle) if cycle else None
        except ValueError as exc:
            raise InvalidError(str(exc)) from exc
        ref = ref or next(iter(self._repo.list_cycles()), None)
        if ref is None:
            raise NotFoundError("No crawl has run yet, so there is nothing to export.")
        summaries = {
            s.id: s
            for s in self._review.list_items(
                ref.id, None, None, None, None, include_baseline=True
            ).items
        }
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(HEADERS)
        for item in self._repo.list_items(ItemFilter(cycle=ref)):
            s = summaries[item.id]
            decision = latest_final_decision(self._repo.list_decisions(item.id))
            included = bool(
                decision
                and decision.include_in_update
                and decision.treatment != Treatment.NOT_RELEVANT
            )
            row = [
                s.internal_ref,
                s.authority,
                s.page_name,
                s.title,
                s.ref_no or "",
                fmt_date(s.displayed_date, empty=""),
                s.first_seen_at.strftime("%d.%m.%Y %H:%M"),
                s.channel_label,
                s.change_type.value.capitalize() if s.change_type else "",
                s.relevance_label,
                s.relevance_score,
                s.state_label,
                _outcome(decision),
                (decision.dismiss_reason or "") if decision else "",
                decision.treatment.value if decision and decision.treatment else "",
                "Yes" if included else "No",
                s.canonical_url,
                "",  # Compliance marking: blank on purpose
            ]
            writer.writerow([_safe(c) if isinstance(c, str) else c for c in row])
        return out.getvalue().encode("utf-8-sig"), f"captured-items-{ref.id.lower()}.csv"
