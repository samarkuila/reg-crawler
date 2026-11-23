"""Audit service: read and export. Events are written by the repository inside each mutation's
transaction, so there is deliberately no way to add, change or delete one from here."""

import csv
import io
import json
from datetime import date

from app.repositories.base import Repository
from app.schemas.api import AuditOptions, AuditPage, AuditRow
from app.schemas.entities import AuditEvent
from app.services.errors import InvalidError
from app.services.readable import key_values

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
        action: str | None = None,
    ) -> list[AuditEvent]:
        events = self._repo.list_audit(entity, actor, date_from, date_to)
        if action:
            events = [e for e in events if e.action == action]
        return events

    def view(
        self,
        entity: str | None = None,
        actor: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        action: str | None = None,
    ) -> AuditPage:
        """Events with before/after turned into readable key/value rows (read only)."""
        if date_from and date_to and date_from > date_to:
            raise InvalidError("The From date must not be after the To date.")
        events = self.list_events(entity, actor, date_from, date_to, action)
        everything = self._repo.list_audit()
        rows = [
            AuditRow(
                id=e.id,
                ts=e.ts,
                actor=e.actor,
                action=e.action,
                action_label=e.action.replace(".", " ").replace("_", " ").capitalize(),
                entity=e.entity,
                entity_id=e.entity_id,
                before=key_values(e.before),
                after=key_values(e.after),
            )
            for e in events
        ]
        return AuditPage(
            total=len(rows),
            rows=rows,
            options=AuditOptions(
                actors=sorted({e.actor for e in everything}),
                entities=sorted({e.entity for e in everything}),
                actions=sorted({e.action for e in everything}),
            ),
        )

    def export_csv(
        self,
        entity: str | None = None,
        actor: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        action: str | None = None,
    ) -> str:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_COLUMNS)
        for e in self.list_events(entity, actor, date_from, date_to, action):
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
