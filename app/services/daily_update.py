"""Daily update service: build the draft from reviewed items, reorder, approve, export.

Only items with a recorded, final review decision that says "include" reach the email (rule 5).
The system never sends: export gives a person an unsent .eml/.html file.
"""

import re
import uuid
from dataclasses import dataclass

from app.repositories.base import Repository
from app.schemas.api import (
    CycleRef,
    DailyUpdatePreview,
    DailyUpdateRow,
    ItemFilter,
    PreCheck,
)
from app.schemas.entities import (
    AppUser,
    DailyUpdate,
    DailyUpdateItem,
    RegulatoryItem,
    ReviewDecision,
)
from app.schemas.enums import CheckStatus, ItemState, Treatment, UpdateStatus, UserRole
from app.services.email_render import STATUS_LEGEND, EmailRenderService
from app.services.errors import ConflictError, ForbiddenError, InvalidError, NotFoundError
from app.services.formatting import fmt_date, fmt_time
from app.services.permissions import APPROVER_ROLES, REVIEW_ROLES, require_role
from app.services.review import latest_final_decision

SYSTEM_ACTOR = "system"
_APPLICABILITY = {"YES": "Yes", "NO": "No", "TBC": "TBC"}


def _date_note(d: ReviewDecision) -> str | None:
    """FR-33: name both dates only when the issue date differs from the website publish date."""
    if d.issue_date and d.publish_date and d.issue_date != d.publish_date:
        return f"Date: {fmt_date(d.issue_date)} · Published on website: {fmt_date(d.publish_date)}"
    return None


@dataclass
class ExportFile:
    content: bytes
    filename: str
    media_type: str


class DailyUpdateService:
    def __init__(self, repo: Repository, renderer: EmailRenderService) -> None:
        self._repo = repo
        self._render = renderer

    # --- building ----------------------------------------------------------------------------

    def _cycle(self, text: str | None) -> CycleRef:
        if text:
            try:
                return CycleRef.parse(text)
            except ValueError as exc:
                raise InvalidError(str(exc)) from exc
        cycles = self._repo.list_cycles()
        if not cycles:
            raise NotFoundError("No crawl has run yet, so there is no update to preview.")
        return cycles[0]

    def _source_status(self, ref: CycleRef) -> tuple[list[str], list[str], list[str]]:
        """(authorities read normally, authorities with a problem, plain-language notes)."""
        runs = self._repo.find_runs(ref)
        if not runs:
            return [], [], []
        sources = {s.id: s for s in self._repo.list_sources()}
        ok: list[str] = []
        failed: list[str] = []
        notes: list[str] = []
        by_source: dict[str, list] = {}
        for c in self._repo.list_source_checks(runs[0].id):
            by_source.setdefault(c.source_id, []).append(c)
        for source_id, checks in by_source.items():
            authority = sources[source_id].authority if source_id in sources else source_id
            bad = [c for c in checks if c.status != CheckStatus.OK]
            if not bad:
                ok.append(authority)
                continue
            failed.append(authority)
            for c in bad:
                manual = self._repo.get_manual_check(c.id)
                if manual:
                    notes.append(
                        f"{authority} checked manually by {manual.user} at {fmt_time(manual.ts)}."
                    )
                else:
                    notes.append(
                        f"{authority} could not be checked automatically "
                        f"({c.status.value.lower().replace('_', ' ')}) "
                        "and has not been checked manually."
                    )
        return ok, failed, list(dict.fromkeys(notes))

    def _unacknowledged(self, ref: CycleRef) -> list[str]:
        runs = self._repo.find_runs(ref)
        if not runs:
            return []
        sources = {s.id: s for s in self._repo.list_sources()}
        out: list[str] = []
        for c in self._repo.list_source_checks(runs[0].id):
            if c.status != CheckStatus.OK and self._repo.get_manual_check(c.id) is None:
                name = sources[c.source_id].authority if c.source_id in sources else c.source_id
                out.append(f"{name} ({c.status.value.lower().replace('_', ' ')})")
        return list(dict.fromkeys(out))

    def _included(self, ref: CycleRef) -> list[tuple[RegulatoryItem, ReviewDecision]]:
        out = []
        for item in self._repo.list_items(ItemFilter(cycle=ref)):
            if item.state not in (ItemState.DECIDED, ItemState.PUBLISHED):
                continue
            decision = latest_final_decision(self._repo.list_decisions(item.id))
            if (
                decision
                and decision.include_in_update
                and decision.treatment != Treatment.NOT_RELEVANT
            ):
                out.append((item, decision))
        return out

    def _row(self, serial: int, item: RegulatoryItem, d: ReviewDecision) -> DailyUpdateRow:
        legend = STATUS_LEGEND.get(d.status_colour) if d.status_colour else None
        return DailyUpdateRow(
            serial_no=serial,
            item_id=item.id,
            decision_id=d.id,
            ref_no_and_date=d.ref_no_and_date or item.title,
            date_note=_date_note(d),
            authority=d.authority or "",
            guideline_title=d.guideline_title or item.title,
            link=item.canonical_url,
            applicability=_APPLICABILITY.get(d.applicability.value, "") if d.applicability else "",
            effective_date=fmt_date(d.effective_date),
            status_colour=d.status_colour,
            status_label=legend[0] if legend else None,
            status_hex=legend[1] if legend else None,
            impact_text=d.impact_text or "",
        )

    def _rows_for(self, update: DailyUpdate) -> list[DailyUpdateRow]:
        return self._rows_from(self._repo.list_daily_update_items(update.id))

    def _recipients(
        self, rows: list[DailyUpdateRow]
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
        to = [
            (r.name, r.email)
            for r in self._repo.list_recipients()
            if r.active and r.kind.value == "TO"
        ]
        cc = [
            (r.name, r.email)
            for r in self._repo.list_recipients()
            if r.active and r.kind.value == "CC"
        ]
        functions = {f.id: f for f in self._repo.list_sme_functions()}
        for row in rows:
            decision = next(
                (d for d in self._repo.list_decisions(row.item_id) if d.id == row.decision_id), None
            )
            fn = (
                functions.get(decision.sme_function) if decision and decision.sme_function else None
            )
            if fn and fn.head_email and (fn.head_name or fn.name, fn.head_email) not in cc:
                cc.append((fn.head_name or fn.name, fn.head_email))
        return to, cc

    def _html(self, update: DailyUpdate, rows: list[DailyUpdateRow], notes: list[str]) -> str:
        return self._render.render_html(
            self._repo.get_email_template(),
            subject=update.subject,
            cycle_date=update.cycle_date,
            rows=[{**r.model_dump(), "link_text": re.sub(r"^https?://", "", r.link)} for r in rows],
            source_notes=notes,
        )

    def _sync(self, ref: CycleRef) -> DailyUpdate:
        """Create or refresh the DRAFT from the current decisions. Approved updates are frozen."""
        existing = self._repo.find_daily_update(ref)
        if existing is not None and existing.status != UpdateStatus.DRAFT:
            return existing
        included = self._included(ref)
        order = (
            {r.item_id: r.serial_no for r in self._repo.list_daily_update_items(existing.id)}
            if existing
            else {}
        )
        included.sort(key=lambda pair: (order.get(pair[0].id, 10_000), pair[1].decided_at))
        template = self._repo.get_email_template()
        ok, failed, notes = self._source_status(ref)
        preparers = list(
            dict.fromkeys(
                [*(existing.prepared_by if existing else []), *(d.reviewer for _, d in included)]
            )
        )
        update = DailyUpdate(
            id=existing.id if existing else f"upd-{uuid.uuid4().hex[:10]}",
            cycle_date=ref.cycle_date,
            cycle=ref.cycle,
            status=UpdateStatus.DRAFT,
            subject=self._render.subject(template, ref.cycle_date, ref.cycle),
            sources_ok=ok,
            sources_failed=failed,
            prepared_by=preparers,
        )
        rows = [
            DailyUpdateItem(update_id=update.id, item_id=i.id, decision_id=d.id, serial_no=n)
            for n, (i, d) in enumerate(included, start=1)
        ]
        update.html = self._html(
            update,
            [self._row(r.serial_no, i, d) for r, (i, d) in zip(rows, included, strict=True)],
            notes,
        )
        unchanged = (
            existing is not None
            and existing.model_dump(exclude={"id"}) == update.model_dump(exclude={"id"})
            and [
                (r.item_id, r.decision_id) for r in self._repo.list_daily_update_items(existing.id)
            ]
            == [(r.item_id, r.decision_id) for r in rows]
        )
        return (
            existing
            if unchanged and existing
            else self._repo.save_daily_update(update, rows, SYSTEM_ACTOR)
        )

    # --- preview -----------------------------------------------------------------------------

    def preview(self, user: AppUser, cycle: str | None) -> DailyUpdatePreview:
        ref = self._cycle(cycle)
        return self._preview(user, self._sync(ref), ref)

    def _preview(self, user: AppUser, update: DailyUpdate, ref: CycleRef) -> DailyUpdatePreview:
        rows = self._rows_for(update)
        _, _, notes = self._source_status(ref)
        to, cc = self._recipients(rows)
        unacked = self._unacknowledged(ref)
        checks = [PreCheck(ok=True, text="Every row has a review decision")]
        if unacked:
            checks.append(
                PreCheck(
                    ok=False,
                    text="Not yet checked: "
                    + "; ".join(unacked)
                    + ". Mark each as checked manually first.",
                )
            )
        else:
            checks.append(
                PreCheck(
                    ok=True, text="All sources checked" + (" – " + " ".join(notes) if notes else "")
                )
            )
        problem = self._approval_problem(user, update, unacked)
        if update.status != UpdateStatus.DRAFT:
            checks.append(PreCheck(ok=True, text=f"Approved by {update.approved_by}"))
        elif problem:
            checks.append(PreCheck(ok=False, text=problem))
        else:
            checks.append(PreCheck(ok=True, text="You can approve this update"))
        return DailyUpdatePreview(
            id=update.id,
            cycle=ref.id,
            status=update.status,
            subject=update.subject,
            to=[n for n, _ in to],
            cc=[n for n, _ in cc],
            rows=rows,
            no_update=not rows,
            html=self._html(update, rows, notes),
            checks=checks,
            can_approve=update.status == UpdateStatus.DRAFT and problem is None,
            approved_by=update.approved_by,
            approved_at=update.approved_at,
        )

    def _approval_problem(
        self, user: AppUser, update: DailyUpdate, unacked: list[str]
    ) -> str | None:
        if user.role not in APPROVER_ROLES:
            approvers = [u.name for u in self._repo.list_users() if u.role == UserRole.APPROVER]
            who = f" ({', '.join(approvers)})" if approvers else ""
            return f"Approver needed: a second person must approve{who}"
        if user.name in update.prepared_by:
            return "You prepared this update, so a different approver must approve it"
        if unacked:
            return "Mark the unchecked sources as checked manually before approving"
        return None

    # --- reorder, approve, export ------------------------------------------------------------

    def _draft(self, update_id: str) -> DailyUpdate:
        update = self._repo.get_daily_update(update_id)
        if update is None:
            raise NotFoundError(f"Daily update {update_id} not found")
        return update

    def reorder(self, user: AppUser, update_id: str, item_ids: list[str]) -> DailyUpdatePreview:
        require_role(user, REVIEW_ROLES, "change the order of rows")
        update = self._draft(update_id)
        if update.status != UpdateStatus.DRAFT:
            raise ConflictError("This update is already approved and cannot be reordered.")
        ref = CycleRef(cycle_date=update.cycle_date, cycle=update.cycle)
        update = self._sync(ref)  # make sure the rows are current before comparing
        current = self._repo.list_daily_update_items(update.id)
        if sorted(item_ids) != sorted(r.item_id for r in current) or len(set(item_ids)) != len(
            item_ids
        ):
            raise InvalidError("The order must list every row in the update exactly once.")
        by_item = {r.item_id: r for r in current}
        rows = [
            DailyUpdateItem(
                update_id=update.id, item_id=i, decision_id=by_item[i].decision_id, serial_no=n
            )
            for n, i in enumerate(item_ids, start=1)
        ]
        update.prepared_by = list(dict.fromkeys([*update.prepared_by, user.name]))
        update.html = self._html(update, self._rows_from(rows), self._source_status(ref)[2])
        saved = self._repo.save_daily_update(update, rows, user.name)
        return self._preview(user, saved, ref)

    def _rows_from(self, rows: list[DailyUpdateItem]) -> list[DailyUpdateRow]:
        out = []
        for r in rows:
            item = self._repo.get_item(r.item_id)
            decision = next(
                (d for d in self._repo.list_decisions(r.item_id) if d.id == r.decision_id), None
            )
            if item and decision:
                out.append(self._row(r.serial_no, item, decision))
        return out

    def approve(self, user: AppUser, update_id: str) -> DailyUpdatePreview:
        require_role(user, APPROVER_ROLES, "approve the daily update")
        update = self._draft(update_id)
        if update.status != UpdateStatus.DRAFT:
            raise ConflictError("This update is already approved.")
        ref = CycleRef(cycle_date=update.cycle_date, cycle=update.cycle)
        update = self._sync(ref)  # approve exactly what the reviewers have decided right now
        if user.name in update.prepared_by:
            raise ForbiddenError("A different approver must approve this update. You prepared it.")
        unacked = self._unacknowledged(ref)
        if unacked:
            raise ConflictError(
                "These sources have not been checked: "
                + "; ".join(unacked)
                + ". Mark each as checked manually before approving."
            )
        approved = self._repo.approve_daily_update(update.id, user.name, user.name)
        return self._preview(user, approved, ref)

    def export(self, user: AppUser, update_id: str, kind: str) -> ExportFile:
        require_role(user, REVIEW_ROLES, "export the daily update")
        update = self._draft(update_id)
        if update.status == UpdateStatus.DRAFT:
            raise ConflictError("Approve the update before exporting it.")
        ref = CycleRef(cycle_date=update.cycle_date, cycle=update.cycle)
        rows = self._rows_for(update)
        html = self._html(update, rows, self._source_status(ref)[2])
        update = self._repo.mark_daily_update_exported(update.id, user.name)
        stem = f"daily-regulatory-update-{ref.id.lower()}"
        if kind == "html":
            return ExportFile(html.encode("utf-8"), f"{stem}.html", "text/html; charset=utf-8")
        to, cc = self._recipients(rows)
        eml = self._render.build_eml(subject=update.subject, html=html, to=to, cc=cc)
        return ExportFile(eml, f"{stem}.eml", "message/rfc822")
