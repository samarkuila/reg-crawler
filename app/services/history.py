"""Item history (FR-27): search every item and show its six-step evidence chain.

email → review decision → document → change detected → listing snapshot → source configuration.
Every step is always listed; a stage that has not happened yet says so instead of disappearing.
"""

import csv
import io
from datetime import date, datetime

from app.repositories.base import Repository
from app.schemas.api import (
    HistoryHit,
    HistoryResults,
    ItemFilter,
    Trail,
    TrailBadge,
    TrailHash,
    TrailLink,
    TrailStep,
)
from app.schemas.entities import (
    Document,
    ItemObservation,
    RegulatoryItem,
    ReviewDecision,
    Source,
)
from app.schemas.enums import ChangeType, Channel, FetchStatus, Treatment, UpdateStatus
from app.services.email_render import STATUS_LEGEND
from app.services.errors import InvalidError, NotFoundError
from app.services.formatting import CYCLE_NAMES, fmt_date, fmt_time
from app.services.review import latest_final_decision
from app.services.stored_copy import StoredFile, load_verified

TREATMENT_LABELS = {
    Treatment.INFO_ONLY: "Information only",
    Treatment.OTHER_FUNCTION: "Other function (SME)",
    Treatment.FUTURE_POTENTIAL: "Future / potential",
    Treatment.IMPLEMENTATION_REQUIRED: "Implementation required",
    Treatment.NOT_RELEVANT: "Not relevant",
}
FIELD_LABELS = {
    "ref_no_and_date": "Reference No. and Date",
    "authority": "Authority",
    "guideline_title": "Guideline title",
    "issue_date": "Issue date",
    "publish_date": "Publish date",
    "effective_date": "Effective date",
    "applicability": "Applicability",
    "status_colour": "Status",
    "impact_text": "Impact on TVSCS",
}
_CHANGE_BADGE = {
    ChangeType.NEW: "new",
    ChangeType.MODIFIED: "mod",
    ChangeType.BACKDATED: "mod",
    ChangeType.REAPPEARED: "grey",
}
_UPDATE_BADGE = {
    UpdateStatus.DRAFT: ("grey", "Draft"),
    UpdateStatus.APPROVED: ("ok", "Approved"),
    UpdateStatus.EXPORTED: ("ok", "Exported"),
}
CSV_HEADERS = ["Step", "Stage", "What happened", "Detail", "Who", "When", "SHA-256", "Link"]


def _short(sha: str) -> str:
    return f"{sha[:6]}…{sha[-4:]}"


def _when(dt: datetime | None) -> str:
    return f"{fmt_date(dt.date())} {fmt_time(dt)}" if dt else ""


class HistoryService:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

    # --- search ------------------------------------------------------------------------------

    def _latest_obs(self, item_id: str) -> tuple[ItemObservation, date] | None:
        runs = {r.id: r for r in self._repo.list_runs()}
        obs = [o for o in self._repo.list_observations(item_id) if o.run_id in runs]
        if not obs:
            return None
        latest = max(obs, key=lambda o: runs[o.run_id].started_at)
        return latest, runs[latest.run_id].cycle_date

    def _active_dates(self, item: RegulatoryItem) -> set[date]:
        runs = {r.id: r for r in self._repo.list_runs()}
        dates = {item.first_seen_at.date(), item.last_seen_at.date()}
        for o in self._repo.list_observations(item.id):
            if o.run_id in runs:
                dates.add(runs[o.run_id].cycle_date)
        return dates

    def search(
        self,
        q: str | None,
        authority: str | None,
        date_from: date | None,
        date_to: date | None,
    ) -> HistoryResults:
        if date_from and date_to and date_from > date_to:
            raise InvalidError("The From date must not be after the To date.")
        sources = {s.id: s for s in self._repo.list_sources()}
        hits: list[tuple[datetime, HistoryHit]] = []
        for item in self._repo.list_items(ItemFilter(q=q.strip() if q else None)):
            source = sources.get(item.source_id)
            name = source.authority if source else item.source_id
            if authority and name != authority:
                continue
            if date_from or date_to:
                dates = self._active_dates(item)
                if not any(
                    (not date_from or d >= date_from) and (not date_to or d <= date_to)
                    for d in dates
                ):
                    continue
            latest = self._latest_obs(item.id)
            if latest and latest[0].change_type != ChangeType.NEW:
                note = f"{latest[0].change_type.value.title()} {fmt_date(latest[1])}"
            else:
                note = f"First seen {fmt_date(item.first_seen_at.date())}"
            hits.append(
                (
                    item.last_seen_at,
                    HistoryHit(
                        id=item.id,
                        title=item.title,
                        authority=name,
                        internal_ref=item.internal_ref,
                        note=note,
                    ),
                )
            )
        hits.sort(key=lambda pair: pair[0], reverse=True)
        authorities = sorted({s.authority for s in sources.values()})
        return HistoryResults(total=len(hits), hits=[h for _, h in hits], authorities=authorities)

    # --- the chain ---------------------------------------------------------------------------

    def trail(self, item_id: str) -> Trail:
        item = self._repo.get_item(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        source = self._repo.get_source(item.source_id)
        if source is None:
            raise NotFoundError(f"Source {item.source_id} not found")
        decisions = self._repo.list_decisions(item_id)
        steps = [
            self._email_step(item),
            self._decision_step(decisions),
            self._document_step(item_id),
            self._change_step(item),
            self._snapshot_step(item, source),
            self._config_step(source),
            self._agent_step(item_id),
        ]
        for n, step in enumerate(steps, start=1):
            step.number = n
        return Trail(item_id=item.id, internal_ref=item.internal_ref, title=item.title, steps=steps)

    def _email_step(self, item: RegulatoryItem) -> TrailStep:
        updates = self._repo.list_daily_updates_for_item(item.id)
        if not updates:
            return TrailStep(
                number=0,
                kind="email",
                title="Not in an email yet",
                text="An item reaches the daily update only after a reviewer accepts it and "
                "includes it.",
                present=False,
            )
        update = updates[-1]
        row = next(
            (r for r in self._repo.list_daily_update_items(update.id) if r.item_id == item.id), None
        )
        tone, label = _UPDATE_BADGE[update.status]
        text = update.subject + (f", row {row.serial_no}" if row else "")
        if update.approved_by and update.approved_at:
            text += f" · approved by {update.approved_by} at {fmt_time(update.approved_at)}"
        cycle = f"{update.cycle_date.isoformat()}-{update.cycle.value}"
        return TrailStep(
            number=0,
            kind="email",
            title="Included in email",
            badge=TrailBadge(tone=tone, label=label),  # type: ignore[arg-type]
            text=text,
            links=[TrailLink(label="Open email draft", href=f"/email?cycle={cycle}")],
            at=update.approved_at,
            who=update.approved_by,
        )

    def _decision_step(self, decisions: list[ReviewDecision]) -> TrailStep:
        final = latest_final_decision(decisions)
        if final is None:
            draft = decisions[-1] if decisions else None
            return TrailStep(
                number=0,
                kind="decision",
                title="Review decision",
                text=(
                    f"Draft saved by {draft.reviewer} on {_when(draft.decided_at)}. Not final yet."
                    if draft
                    else "No review decision has been recorded yet."
                ),
                present=False,
            )
        dismissed = final.treatment == Treatment.NOT_RELEVANT
        parts = [
            f"{'Dismissed' if dismissed else 'Accepted'} by {final.reviewer} "
            f"on {_when(final.decided_at)}"
        ]
        if dismissed and final.dismiss_reason:
            parts.append(f"Reason: {final.dismiss_reason}")
        if not dismissed:
            if final.treatment:
                parts.append(f"Treatment: {TREATMENT_LABELS[final.treatment]}")
            if final.applicability:
                parts.append(f"Applicability: {final.applicability.value.capitalize()}")
            if final.status_colour:
                parts.append(f"Status: {STATUS_LEGEND[final.status_colour][0]}")
        edited = [
            FIELD_LABELS.get(k, k.replace("_", " ").capitalize()) for k in final.fields_edited
        ]
        note = (
            "Fields edited by reviewer: " + ", ".join(edited)
            if edited
            else "No pre-filled fields were edited."
        )
        return TrailStep(
            number=0,
            kind="decision",
            title="Review decision",
            text=" · ".join(parts),
            note=note,
            at=final.decided_at,
            who=final.reviewer,
        )

    def _document_step(self, item_id: str) -> TrailStep:
        docs = self._repo.list_documents(item_id)
        if not docs:
            return TrailStep(
                number=0,
                kind="document",
                title="No document stored",
                text="Only the listing entry was captured for this item.",
                present=False,
            )
        stored = [d for d in docs if d.sha256]
        doc: Document = stored[-1] if stored else docs[-1]
        if doc.fetch_status == FetchStatus.BLOCKED and not doc.sha256:
            return TrailStep(
                number=0,
                kind="document",
                title="Document needs manual fetch",
                badge=TrailBadge(tone="mod", label="Blocked"),
                text="The download was blocked and has not been supplied yet. "
                "It is never bypassed.",
                at=doc.fetched_at,
            )
        manual = doc.fetch_status == FetchStatus.MANUAL
        text = (
            f"Manually supplied by {doc.uploaded_by or 'a reviewer'}"
            f"{f' ({doc.filename})' if doc.filename else ''}"
            if manual
            else f"{doc.filename or 'Page'} saved"
        )
        if doc.fetched_at:
            text += f" {_when(doc.fetched_at)}"
        if doc.text_path:
            text += " · text extracted"
        hashes = (
            [TrailHash(label="SHA-256", sha256=doc.sha256, short=_short(doc.sha256))]
            if doc.sha256
            else []
        )
        links = (
            [TrailLink(label="Open stored copy", href=f"/api/v1/documents/{doc.id}/content")]
            if doc.sha256 and self._repo.read_blob(doc.sha256) is not None
            else []
        )
        return TrailStep(
            number=0,
            kind="document",
            title="Document stored",
            text=text,
            hashes=hashes,
            links=links,
            at=doc.fetched_at,
            who=doc.uploaded_by,
        )

    def _agent_step(self, item_id: str) -> TrailStep:
        """Luna's part: which model, prompt and guidance version produced the suggestions."""
        done = self._repo.list_extractions(item_id)
        if not done:
            return TrailStep(
                number=0,
                kind="agent",
                title="Luna suggestion",
                text="No suggestion from Luna for this item (Luna was off or had nothing to add).",
                present=False,
            )
        last = done[-1]
        tasks = " and ".join(sorted({d.task.value.lower() for d in done}))
        guidance = f", guidance version {last.guidance_version}" if last.guidance_version else ""
        return TrailStep(
            number=0,
            kind="agent",
            title="Suggested by Luna",
            badge=TrailBadge(tone="grey", label="Suggestion only"),
            text=(
                f"Luna ({last.model}) made a {tasks} suggestion with prompt "
                f"{last.prompt_version}{guidance}. A person decides; nothing was applied."
            ),
            at=last.created_at,
            who=f"agent:luna ({last.model})",
        )

    def _runs_by_id(self) -> dict:  # type: ignore[type-arg]
        return {r.id: r for r in self._repo.list_runs()}

    def _change_step(self, item: RegulatoryItem) -> TrailStep:
        latest = self._latest_obs(item.id)
        if latest is None:
            return TrailStep(
                number=0,
                kind="change",
                title="Change detected",
                text="No detection has been recorded for this item.",
                present=False,
            )
        obs, _ = latest
        seen = "RSS and HTML" if item.channel == Channel.BOTH else obs.channel.value
        first = fmt_date(item.first_seen_at.date())
        text = (
            f"Same fingerprint as {first}, content hash changed · seen in {seen}"
            if obs.prior_hash
            else f"New fingerprint, first seen {first} · seen in {seen}"
        )
        hashes = []
        if obs.prior_hash:
            hashes.append(
                TrailHash(label="Before", sha256=obs.prior_hash, short=_short(obs.prior_hash))
            )
        hashes.append(
            TrailHash(label="After", sha256=obs.current_hash, short=_short(obs.current_hash))
        )
        return TrailStep(
            number=0,
            kind="change",
            title="Change detected",
            badge=TrailBadge(
                tone=_CHANGE_BADGE[obs.change_type],  # type: ignore[arg-type]
                label=obs.change_type.value.title(),
            ),
            text=text,
            hashes=hashes,
        )

    def _snapshot_step(self, item: RegulatoryItem, source: Source) -> TrailStep:
        runs = self._runs_by_id()
        obs = [
            o for o in self._repo.list_observations(item.id) if o.snapshot_id and o.run_id in runs
        ]
        obs.sort(key=lambda o: runs[o.run_id].started_at)
        snap = self._repo.get_snapshot(obs[-1].snapshot_id) if obs and obs[-1].snapshot_id else None
        if snap is None:
            return TrailStep(
                number=0,
                kind="snapshot",
                title="Listing snapshot",
                text="No listing snapshot is linked to this item.",
                present=False,
            )
        run = runs.get(obs[-1].run_id)
        by = f" by the {CYCLE_NAMES[run.cycle]} run" if run else ""
        links = (
            [TrailLink(label="Open snapshot", href=f"/api/v1/snapshots/{snap.id}/content")]
            if self._repo.read_blob(snap.sha256) is not None
            else []
        )
        return TrailStep(
            number=0,
            kind="snapshot",
            title="Listing snapshot",
            text=f"{source.authority} {source.page_name} page fetched {_when(snap.fetched_at)}{by}",
            hashes=[TrailHash(label="SHA-256", sha256=snap.sha256, short=_short(snap.sha256))],
            links=links,
            at=snap.fetched_at,
        )

    def _config_step(self, source: Source) -> TrailStep:
        version = self._repo.get_source_version(source.id, source.current_version)
        if version is None:
            return TrailStep(
                number=0,
                kind="source_config",
                title="Source configuration",
                text="No approved configuration version is recorded for this source.",
                present=False,
            )
        when = version.decided_at or version.ts
        text = f"{source.authority} · {source.page_name}, version {version.version}"
        if version.approved_by:
            text += f" · approved by {version.approved_by} on {fmt_date(when.date())}"
        return TrailStep(
            number=0,
            kind="source_config",
            title="Source configuration",
            text=text,
            links=[TrailLink(label="View source settings", href="/sources")],
            at=when,
            who=version.approved_by,
        )

    # --- export and stored copy --------------------------------------------------------------

    def trail_csv(self, item_id: str) -> tuple[bytes, str]:
        trail = self.trail(item_id)
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(CSV_HEADERS)
        for s in trail.steps:
            writer.writerow(
                [
                    s.number,
                    s.kind,
                    s.title + (f" ({s.badge.label})" if s.badge else ""),
                    s.text + (f" | {s.note}" if s.note else ""),
                    s.who or "",
                    _when(s.at),
                    "; ".join(f"{h.label} {h.sha256}" for h in s.hashes),
                    "; ".join(link.href for link in s.links),
                ]
            )
        return out.getvalue().encode("utf-8-sig"), f"trail-{trail.internal_ref.lower()}.csv"

    def document_file(self, document_id: str) -> StoredFile:
        doc = self._repo.get_document(document_id)
        if doc is None:
            raise NotFoundError(f"Document {document_id} not found")
        return load_verified(self._repo, doc.sha256, doc.filename or f"{doc.id}.txt", doc.mime)
