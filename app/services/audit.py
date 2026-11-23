"""Audit service: read and export. Events are written by the repository inside each mutation's
transaction, so there is deliberately no way to add, change or delete one from here."""

import csv
import io
import json
from datetime import date

from app.repositories.base import Repository
from app.schemas.entities import AuditEvent

CSV_COLUMNS = ["id", "ts", "actor", "action", "entity", "entity_id", "before", "after"]


def _safe(value: str) -> str:
    """Stop spreadsheet apps from running a cell that starts like a formula."""
    return "'" + value if value[:1] in ("=", "+", "-", "@", "\t", "\r") else value


class AuditService:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def list_events(
        self,
        entity: str | None = None,
        actor: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> list[AuditEvent]:
        return self._repo.list_audit(entity, actor, date_from, date_to)

    def export_csv(
        self,
        entity: str | None = None,
        actor: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
    ) -> str:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        for e in self.list_events(entity, actor, date_from, date_to):
            writer.writerow(
                [
                    _safe(e.id),
                    e.ts.isoformat(),
                    _safe(e.actor),
                    _safe(e.action),
                    _safe(e.entity),
                    _safe(e.entity_id),
                    _safe(json.dumps(e.before, ensure_ascii=False, default=str)),
                    _safe(json.dumps(e.after, ensure_ascii=False, default=str)),
                ]
            )
        return out.getvalue()
