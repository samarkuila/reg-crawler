"""Alerts after a run: what a person should look at (banner now; Teams/email in V2)."""

from datetime import timedelta

from app.repositories.base import Repository
from app.schemas.entities import Alert, now_ist
from app.services import readable


class AlertService:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    def latest(self, run_id: str | None = None) -> list[Alert]:
        """Alerts of one run (default: the latest run), urgent first.

        Alerts that belong to no run (a scheduled run that was skipped) are included for 24 hours.
        """
        if run_id is None:
            runs = sorted(self._repo.list_runs(), key=lambda r: r.started_at, reverse=True)
            run_id = runs[0].id if runs else None
        found = self._repo.list_alerts(run_id) if run_id else []
        cutoff = now_ist() - timedelta(hours=24)
        found += [
            a for a in self._repo.list_alerts() if a.run_id is None and a.created_at >= cutoff
        ]
        order = {"urgent": 0, "warning": 1}
        found.sort(key=lambda a: (order[a.severity], a.created_at, a.id))
        return [
            a.model_copy(update={"message": readable.plain_error(a.message) or a.message})
            for a in found
        ]
