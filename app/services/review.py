"""Review service: runs and source health, item queue, decisions, uploads, manual checks.

Business rules live here (roles, validation, state moves). The repository only stores.
"""

import hashlib
import re
import uuid
from datetime import date

from app.agents import settings as agent_settings
from app.repositories.base import Repository
from app.schemas.api import (
    ApplicabilitySuggestion,
    BulkDismissIn,
    BulkDismissOut,
    CycleRef,
    DecisionIn,
    DecisionOut,
    ItemCounts,
    ItemDetail,
    ItemFilter,
    ItemListResponse,
    ItemSection,
    ItemSummary,
    LunaChip,
    LunaSuggestions,
    Prefill,
    RunDetail,
    SourceCheckView,
    SourceHealthSummary,
    TraceStep,
)
from app.schemas.entities import (
    AppUser,
    CrawlRun,
    Document,
    ItemObservation,
    ManualCheck,
    RegulatoryItem,
    ReviewDecision,
    Source,
    SourceCheck,
    now_ist,
)
from app.schemas.enums import (
    ChangeType,
    Channel,
    CheckStatus,
    FetchStatus,
    FlagStatus,
    FlagType,
    ItemOrigin,
    ItemState,
    Priority,
    Treatment,
)
from app.services import readable
from app.services.errors import (
    ConflictError,
    InvalidError,
    NotFoundError,
    TooLargeError,
)
from app.services.first_run import FirstRunConfig, load_first_run_config
from app.services.formatting import CYCLE_NAMES, fmt_date, fmt_time
from app.services.permissions import REVIEW_ROLES, require_role
from app.services.prefill_rules import applicability_hint, effective_date

MAX_UPLOAD_BYTES = 20 * 1024 * 1024
STATUS_LABELS = {
    CheckStatus.OK: "OK",
    CheckStatus.FAILED: "Failed",
    CheckStatus.BLOCKED: "Blocked",
    CheckStatus.PARSE_ERROR: "Parse error",
    CheckStatus.ZERO_ITEMS: "Zero items",
}
ITEM_STATE_LABELS = {
    ItemState.DETECTED: "Pending",
    ItemState.ENRICHED: "Pending",
    ItemState.NEEDS_MANUAL_FETCH: "Needs manual fetch",
    ItemState.IN_REVIEW: "In review",
    ItemState.DECIDED: "Decided",
    ItemState.PUBLISHED: "Published",
}
DECIDED_STATES = {ItemState.DECIDED, ItemState.PUBLISHED}
_PREFILLED_FIELDS = (
    "ref_no_and_date",
    "authority",
    "guideline_title",
    "issue_date",
    "publish_date",
    "effective_date",
)


def latest_final_decision(decisions: list[ReviewDecision]) -> ReviewDecision | None:
    final = [d for d in decisions if not d.is_draft]
    return final[-1] if final else None


class ReviewService:
    def __init__(self, repo: Repository, first_run: FirstRunConfig | None = None) -> None:
        self._repo = repo
        self._first_run = first_run  # tests inject it; otherwise settings.yaml is read

    # --- cycles, runs, source health ---------------------------------------------------------

    def list_cycles(self) -> list[CycleRef]:
        return self._repo.list_cycles()

    def resolve_cycle(self, text: str | None) -> CycleRef | None:
        """Parse a cycle id; with no value, the most recent cycle (or None if nothing ran)."""
        if text:
            try:
                return CycleRef.parse(text)
            except ValueError as exc:
                raise InvalidError(str(exc)) from exc
        cycles = self._repo.list_cycles()
        return cycles[0] if cycles else None

    def list_runs(self, cycle_date: date | None) -> list[CrawlRun]:
        return self._repo.list_runs(cycle_date)

    def run_detail(self, run_id: str) -> RunDetail:
        run = self._repo.get_run(run_id)
        if run is None:
            raise NotFoundError(f"Run {run_id} not found")
        checks = self._ordered_checks(run_id)
        active = {s.id for s in self._repo.list_sources() if s.active}
        scope = set(run.sources) if run.sources else active  # a one-source run counts one source
        done = {c.source_id for c in checks}
        return RunDetail(
            run=run,
            checks=[self.check_view(c) for c in checks],
            sources_total=len(scope),
            sources_done=len(done & scope),
        )

    def _ordered_checks(self, run_id: str) -> list[SourceCheck]:
        """Checks in register order (then RSS, HTML, XML), whatever order the repository uses."""
        position = {s.id: i for i, s in enumerate(self._repo.list_sources())}
        channel = {"RSS": 0, "HTML": 1, "XML": 2}
        return sorted(
            self._repo.list_source_checks(run_id),
            key=lambda c: (position.get(c.source_id, len(position)), channel[c.channel.value]),
        )

    def check_view(self, check: SourceCheck) -> SourceCheckView:
        source = self._repo.get_source(check.source_id)
        manual = self._repo.get_manual_check(check.id)
        return SourceCheckView(
            id=check.id,
            run_id=check.run_id,
            source_id=check.source_id,
            authority=source.authority if source else check.source_id,
            page_name=source.page_name if source else "",
            channel=check.channel.value,
            status=check.status,
            status_label=STATUS_LABELS[check.status],
            http_status=check.http_status,
            items_found=check.items_found,
            error_detail=readable.plain_error(check.error_detail),
            snapshot_id=check.snapshot_id,
            stats=check.stats,
            needs_acknowledgement=check.status != CheckStatus.OK and manual is None,
            manual_check=manual,
        )

    def source_health(self, cycle: str | None) -> SourceHealthSummary:
        ref = self.resolve_cycle(cycle)
        active = [s for s in self._repo.list_sources() if s.active]
        if ref is None:
            return SourceHealthSummary(
                cycle="",
                cycle_label="No cycle yet",
                run_id=None,
                sources_total=len(active),
                sources_ok=0,
                sources_manual=0,
                headline="No crawl has run yet",
                checks=[],
                attention=[],
            )
        runs = self._repo.find_runs(ref)
        if not runs:
            raise NotFoundError(f"No run found for cycle {ref.id}")
        run = runs[0]  # newest first
        views = [self.check_view(c) for c in self._ordered_checks(run.id)]
        ok_sources, manual_sources = set(), set()
        for s in active:
            mine = [v for v in views if v.source_id == s.id]
            if not mine:
                continue
            if all(v.status == CheckStatus.OK for v in mine):
                ok_sources.add(s.id)
            elif all(v.status == CheckStatus.OK or v.manual_check for v in mine):
                manual_sources.add(s.id)
        attention = sorted(
            (v for v in views if v.status != CheckStatus.OK),
            key=lambda v: (not v.needs_acknowledgement, v.authority),
        )
        checked = len(ok_sources) + len(manual_sources)
        headline = f"{checked} of {len(active)} sources checked"
        if manual_sources:
            headline += f" ({len(manual_sources)} manually)"
        return SourceHealthSummary(
            cycle=ref.id,
            cycle_label=ref.label,
            run_id=run.id,
            last_run_started_at=run.started_at,
            last_run_ended_at=run.ended_at,
            sources_total=len(active),
            sources_ok=len(ok_sources),
            sources_manual=len(manual_sources),
            headline=headline,
            checks=views,
            attention=attention,
        )

    def manual_check(self, user: AppUser, check_id: str, note: str) -> ManualCheck:
        require_role(user, REVIEW_ROLES, "mark a source as checked manually")
        check = self._repo.get_source_check(check_id)
        if check is None:
            raise NotFoundError(f"Source check {check_id} not found")
        if check.status == CheckStatus.OK:
            raise ConflictError(
                "This source was read normally; there is nothing to check manually."
            )
        if self._repo.get_manual_check(check_id) is not None:
            raise ConflictError("This source has already been marked as checked manually.")
        return self._repo.add_manual_check(check_id, user.name, note, user.name)

    # --- items -------------------------------------------------------------------------------

    def _threshold(self) -> int:
        setting = self._repo.get_setting("low-relevance-threshold")
        return int(setting.value) if setting else 30

    def _latest_obs(self, item: RegulatoryItem, run_ids: set[str] | None) -> ItemObservation | None:
        obs = self._repo.list_observations(item.id)
        if run_ids is not None:
            obs = [o for o in obs if o.run_id in run_ids]
        if not obs:
            return None
        started = {r.id: r.started_at for r in self._repo.list_runs()}
        return max(obs, key=lambda o: started.get(o.run_id, now_ist()))

    def _summary(
        self,
        item: RegulatoryItem,
        sources: dict[str, Source],
        threshold: int,
        run_ids: set[str] | None,
    ) -> ItemSummary:
        source = sources.get(item.source_id)
        obs = self._latest_obs(item, run_ids)
        change = obs.change_type if obs else None
        if obs and obs.channel.value == "XML":
            channel_label = "XML"
        else:
            channel_label = {"RSS": "RSS", "HTML": "HTML", "BOTH": "RSS + HTML"}[item.channel.value]
        badges: list[str] = []
        if item.priority == Priority.URGENT:
            badges.append("URGENT")
        if change:
            badges.append(change.value)
        if item.channel_mismatch:
            badges.append("RSS_ONLY" if item.channel == Channel.RSS else "HTML_ONLY")
        note: str | None = None
        if change == ChangeType.BACKDATED and item.displayed_date:
            published = item.first_seen_at.date()
            note = f"Issued {fmt_date(item.displayed_date)} · published {fmt_date(published)}"
        elif item.channel_mismatch:
            note = (
                "Not yet on the HTML listing – channel mismatch"
                if item.channel == Channel.RSS
                else "Not in the RSS feed – channel mismatch"
            )
        label = (
            "High"
            if item.relevance_score >= 70
            else ("Medium" if item.relevance_score >= threshold else "Low")
        )
        luna = self._luna_chip(item)
        if luna is not None and luna.disagreement:
            badges.append("DISAGREE")
        section: ItemSection
        if item.state == ItemState.NEEDS_MANUAL_FETCH:
            section = "manual_fetch"
        elif item.relevance_score < threshold and item.priority == Priority.NORMAL:
            section = "low_relevance"
        else:
            section = "attention"
        if luna is not None and luna.disagreement and section == "low_relevance":
            section = "attention"  # rules and Luna disagree: raised, never lowered
        return ItemSummary(
            id=item.id,
            internal_ref=item.internal_ref,
            source_id=item.source_id,
            authority=source.authority if source else item.source_id,
            page_name=source.page_name if source else "",
            title=item.title,
            canonical_url=item.canonical_url,
            ref_no=item.ref_no,
            displayed_date=item.displayed_date,
            channel=item.channel,
            channel_label=channel_label,
            channel_mismatch=item.channel_mismatch,
            relevance_score=item.relevance_score,
            relevance_label=label,
            relevance_reason=item.relevance_reason,
            priority=item.priority,
            state=item.state,
            state_label=ITEM_STATE_LABELS[item.state],
            change_type=change,
            badges=badges,
            note=note,
            summary=item.summary,
            section=section,
            decided=item.state in DECIDED_STATES,
            first_seen_at=item.first_seen_at,
            luna=luna,
            origin=item.origin,
            flags=item.flags,
        )

    def list_items(
        self,
        cycle: str | None,
        state: ItemState | None,
        channel: Channel | None,
        q: str | None,
        min_relevance: int | None,
        origin: ItemOrigin | None = None,
        include_baseline: bool | None = None,
    ) -> ItemListResponse:
        """The review queue. Items stored by a source's first crawl (origin BASELINE) are left out
        unless `crawler.first_run.baseline_items_in_review` is true, `origin` asks for them, or
        `include_baseline` says so. They are never deleted: Item history shows them."""
        hide = None
        if origin is None and not (
            include_baseline
            if include_baseline is not None
            else (self._first_run or load_first_run_config()).baseline_items_in_review
        ):
            hide = ItemOrigin.BASELINE
        ref = self.resolve_cycle(cycle)
        sources = {s.id: s for s in self._repo.list_sources()}
        threshold = self._threshold()
        run_ids = {r.id for r in self._repo.find_runs(ref)} if ref else None
        if ref is None and cycle is None:
            base = self._repo.list_items(ItemFilter(exclude_origin=hide, origin=origin))
        else:
            base = self._repo.list_items(ItemFilter(cycle=ref, exclude_origin=hide, origin=origin))
        base_summaries = [self._summary(i, sources, threshold, run_ids) for i in base]
        filtered = self._repo.list_items(
            ItemFilter(
                cycle=ref,
                state=state,
                channel=channel,
                q=q,
                min_relevance=min_relevance,
                origin=origin,
                exclude_origin=hide,
            )
        )
        summaries = [self._summary(i, sources, threshold, run_ids) for i in filtered]
        summaries.sort(
            key=lambda s: (
                s.priority != Priority.URGENT,
                not (s.luna and s.luna.disagreement),  # disagreements sort higher
                -s.relevance_score,
                -(s.displayed_date.toordinal() if s.displayed_date else 0),
                s.title.lower(),
            )
        )
        hidden = (
            len(
                self._repo.list_items(
                    ItemFilter(cycle=ref, origin=ItemOrigin.BASELINE)
                    if ref
                    else ItemFilter(origin=ItemOrigin.BASELINE)
                )
            )
            if hide
            else 0
        )
        return ItemListResponse(
            cycle=ref.id if ref else None,
            counts=self._counts(base_summaries),
            items=summaries,
            baseline_hidden=hidden,
        )

    @staticmethod
    def _counts(items: list[ItemSummary]) -> ItemCounts:
        def n(change: ChangeType) -> int:
            return sum(1 for i in items if i.change_type == change)

        return ItemCounts(
            new=n(ChangeType.NEW),
            modified=n(ChangeType.MODIFIED),
            backdated=n(ChangeType.BACKDATED),
            reappeared=n(ChangeType.REAPPEARED),
            urgent=sum(1 for i in items if i.priority == Priority.URGENT),
            needs_manual_fetch=sum(1 for i in items if i.state == ItemState.NEEDS_MANUAL_FETCH),
            decided=sum(1 for i in items if i.decided),
            total=len(items),
            low_relevance=sum(1 for i in items if i.section == "low_relevance"),
        )

    def _item(self, item_id: str) -> RegulatoryItem:
        item = self._repo.get_item(item_id)
        if item is None:
            raise NotFoundError(f"Item {item_id} not found")
        return item

    # --- Luna (suggestions only) ---------------------------------------------------------------

    def _extractions(self, item: RegulatoryItem):  # type: ignore[no-untyped-def]
        done = self._repo.list_extractions(item.id)
        screen = next((e for e in reversed(done) if e.task.value == "SCREEN"), None)
        extract = next((e for e in reversed(done) if e.task.value == "EXTRACT"), None)
        return screen, extract

    def _luna_chip(self, item: RegulatoryItem) -> LunaChip | None:
        screen, _ = self._extractions(item)
        flags = self._repo.list_agent_flags(item_id=item.id, status=FlagStatus.OPEN)
        disagree = any(f.type == FlagType.DISAGREEMENT for f in flags)
        if screen is None and not disagree:
            return None
        return LunaChip(
            level=screen.nbfc_relevance.value if screen and screen.nbfc_relevance else None,
            score=screen.relevance_score if screen else None,
            disagreement=disagree,
        )

    def luna_suggestions(self, item: RegulatoryItem) -> LunaSuggestions | None:
        screen, extract = self._extractions(item)
        if screen is None and extract is None:
            return None
        flag = next(
            (
                f
                for f in self._repo.list_agent_flags(item_id=item.id, status=FlagStatus.OPEN)
                if f.type == FlagType.DISAGREEMENT
            ),
            None,
        )
        first = screen or extract
        fields = screen.fields if screen else {}
        return LunaSuggestions(
            level=screen.nbfc_relevance.value if screen and screen.nbfc_relevance else None,
            score=screen.relevance_score if screen else None,
            reason=fields.get("reason"),
            addressed_to=list(fields.get("addressed_to") or []),
            treatment=screen.suggested_treatment if screen else None,
            sme_function=screen.suggested_sme_function if screen else None,
            urgency=fields.get("urgency"),
            evidence=list(fields.get("evidence") or []),
            fields=extract.fields if extract else {},
            model=first.model,
            deployment=first.deployment,
            prompt_version=first.prompt_version,
            guidance_version=first.guidance_version,
            created_at=first.created_at,
            disagreement=flag.payload if flag else None,
        )

    def _document_text(self, item: RegulatoryItem) -> str | None:
        """Text of the item's first stored document that has any (read-only)."""
        for doc in self._repo.list_documents(item.id):
            if doc.text_path:
                text = self._repo.read_text(doc.id)
                if text:
                    return text
        return None

    def _prefill(self, item: RegulatoryItem, source: Source, change: ChangeType | None) -> Prefill:
        issue = item.displayed_date
        publish = issue
        if change == ChangeType.BACKDATED:
            publish = item.first_seen_at.date()
        shown = fmt_date(issue)
        if item.ref_no:
            ref = f"{item.ref_no} dated {shown}"
        else:
            ref = f"{item.title} · Date: {shown}" + (
                " (Updated)" if change == ChangeType.MODIFIED else ""
            )
        if change == ChangeType.BACKDATED and publish != issue:
            ref += f" · Published on website: {fmt_date(publish)}"
        text = self._document_text(item)
        found = effective_date(text, issue, publish, source.authority)
        effective, rule = found.value, found.rule
        profile = self._repo.get_setting("entity-profile")
        hint = applicability_hint(text, profile.value if profile else None)
        return Prefill(
            ref_no_and_date=ref,
            authority=source.authority,
            guideline_title=item.title,
            issue_date=issue,
            publish_date=publish,
            effective_date=effective,
            effective_date_rule=rule,
            applicability_hint=(
                ApplicabilitySuggestion(
                    suggested=hint.suggested,
                    text=hint.text,
                    phrase=hint.phrase,
                    profile_item=hint.profile_item,
                )
                if hint
                else None
            ),
        )

    def item_detail(self, item_id: str) -> ItemDetail:
        item = self._item(item_id)
        source = self._repo.get_source(item.source_id)
        if source is None:
            raise NotFoundError(f"Source {item.source_id} not found")
        sources = {s.id: s for s in self._repo.list_sources()}
        summary = self._summary(item, sources, self._threshold(), None)
        observations = self._repo.list_observations(item_id)
        snapshots = [
            s
            for s in (self._repo.get_snapshot(o.snapshot_id) for o in observations if o.snapshot_id)
            if s is not None
        ]
        documents = self._repo.list_documents(item_id)
        decisions = self._repo.list_decisions(item_id)
        version = self._repo.get_source_version(source.id, source.current_version)
        return ItemDetail(
            item=summary,
            prefill=self._prefill(item, source, summary.change_type),
            latest_decision=latest_final_decision(decisions)
            or (decisions[-1] if decisions else None),
            decisions=decisions,
            observations=observations,
            snapshots=snapshots,
            documents=documents,
            source=source,
            source_version=version,
            chain=self._chain(item, source, observations, snapshots, documents, decisions, version),
            luna=self.luna_suggestions(item),
            luna_enabled=agent_settings.agent_enabled(self._repo),
        )

    def _chain(self, item, source, observations, snapshots, documents, decisions, version):  # type: ignore[no-untyped-def]
        """Traceability (FR-27): email, decision, document, detection, snapshot, version, run."""
        steps: list[TraceStep] = []
        for u in self._repo.list_daily_updates_for_item(item.id):
            who = f" · approved by {u.approved_by}" if u.approved_by else ""
            steps.append(
                TraceStep(
                    kind="email",
                    title=u.subject,
                    detail=f"Update status: {u.status.value.title()}{who}",
                    at=u.approved_at,
                )
            )
        decision = latest_final_decision(decisions)
        if decision:
            edited = len(decision.fields_edited)
            steps.append(
                TraceStep(
                    kind="decision",
                    title=f"Reviewed by {decision.reviewer}",
                    detail=(
                        f"{decision.treatment.value if decision.treatment else 'No treatment'} · "
                        f"{'included' if decision.include_in_update else 'not included'}"
                        f" · {edited} pre-filled field{'s' if edited != 1 else ''} edited"
                    ),
                    at=decision.decided_at,
                )
            )
        for d in documents:
            steps.append(
                TraceStep(
                    kind="document",
                    title=f"Document {d.fetch_status.value.lower()}",
                    detail=d.filename or d.url or "",
                    sha256=d.sha256,
                    at=d.fetched_at,
                )
            )
        runs = {r.id: r for r in self._repo.list_runs()}
        for o in sorted(
            observations, key=lambda o: runs[o.run_id].started_at if o.run_id in runs else now_ist()
        ):
            run = runs.get(o.run_id)
            when = f"{fmt_date(run.cycle_date)} {CYCLE_NAMES[run.cycle]}" if run else o.run_id
            steps.append(
                TraceStep(
                    kind="detection",
                    title=f"Detected as {o.change_type.value.title()}",
                    detail=f"{o.channel.value} · {when}",
                    sha256=o.current_hash,
                    at=run.started_at if run else None,
                )
            )
        for s in snapshots:
            steps.append(
                TraceStep(
                    kind="snapshot",
                    title="Listing snapshot",
                    detail=s.url,
                    sha256=s.sha256,
                    at=s.fetched_at,
                )
            )
        if version:
            steps.append(
                TraceStep(
                    kind="source_version",
                    title=f"{source.authority} source configuration v{version.version}",
                    detail=f"Approved by {version.approved_by}"
                    if version.approved_by
                    else "Approved",
                    at=version.decided_at or version.ts,
                )
            )
        first = runs.get(item.first_seen_run_id)
        if first:
            steps.append(
                TraceStep(
                    kind="run",
                    title="First seen in a crawl run",
                    detail=(
                        f"{fmt_date(first.cycle_date)} {CYCLE_NAMES[first.cycle]}"
                        f" · {fmt_time(item.first_seen_at)}"
                    ),
                    at=first.started_at,
                )
            )
        return steps

    # --- decisions ---------------------------------------------------------------------------

    def decide(self, user: AppUser, item_id: str, body: DecisionIn) -> DecisionOut:
        require_role(user, REVIEW_ROLES, "review items")
        item = self._item(item_id)
        source = self._repo.get_source(item.source_id)
        assert source is not None  # noqa: S101 - repository invariant
        if item.state == ItemState.PUBLISHED:
            raise ConflictError("This item is already in an approved update and cannot be changed.")
        if item.state == ItemState.NEEDS_MANUAL_FETCH and body.action != "dismiss":
            raise ConflictError("Upload the document first, or dismiss the item with a reason.")
        summary = self._summary(item, {source.id: source}, self._threshold(), None)
        prefill = self._prefill(item, source, summary.change_type)

        treatment = body.treatment
        include = body.include_in_update
        reason = (body.dismiss_reason or "").strip() or None
        if body.action == "dismiss":
            if not reason:
                raise InvalidError("A reason is required to dismiss an item.")
            treatment, include = Treatment.NOT_RELEVANT, False
        elif body.action == "accept":
            if treatment is None:
                raise InvalidError("Choose a treatment before accepting.")
            if treatment == Treatment.NOT_RELEVANT:
                raise InvalidError("Use Dismiss, with a reason, for items that are not relevant.")
            if include and not (
                body.applicability and body.status_colour and (body.impact_text or "").strip()
            ):
                raise InvalidError(
                    "To include an item in the update, fill in applicability, status and impact."
                )
        if treatment == Treatment.OTHER_FUNCTION:
            if not body.sme_function:
                raise InvalidError("Choose the SME function for an 'Other function' item.")
            if body.sme_function not in {f.id for f in self._repo.list_sme_functions() if f.active}:
                raise InvalidError("Unknown SME function.")
        if include and treatment is None:
            raise InvalidError("Choose a treatment before including an item.")

        values: dict[str, object] = {}
        edited: dict[str, dict[str, object]] = {}
        for field in _PREFILLED_FIELDS:
            proposed = getattr(prefill, field)
            sent = getattr(body, field)
            final = proposed if sent is None else sent
            values[field] = final
            if final != proposed:
                edited[field] = {
                    "prefilled": proposed.isoformat() if isinstance(proposed, date) else proposed,
                    "final": final.isoformat() if isinstance(final, date) else final,
                }
        draft = body.action == "save"
        decision = ReviewDecision(
            id=f"dec-{uuid.uuid4().hex[:10]}",
            item_id=item_id,
            reviewer=user.name,
            decided_at=now_ist(),
            treatment=treatment,
            include_in_update=include,
            applicability=body.applicability,
            status_colour=body.status_colour,
            impact_text=(body.impact_text or "").strip() or None,
            sme_function=body.sme_function if treatment == Treatment.OTHER_FUNCTION else None,
            dismiss_reason=reason if body.action == "dismiss" else None,
            fields_edited=edited,
            is_draft=draft,
            **values,  # type: ignore[arg-type]
        )
        new_state = (
            item.state
            if draft and item.state == ItemState.DECIDED
            else (ItemState.IN_REVIEW if draft else ItemState.DECIDED)
        )
        saved = self._repo.save_decisions([decision], new_state, user.name)[0]
        return DecisionOut(
            decision=saved,
            item=self._summary(self._item(item_id), {source.id: source}, self._threshold(), None),
        )

    def bulk_dismiss(self, user: AppUser, body: BulkDismissIn) -> BulkDismissOut:
        require_role(user, REVIEW_ROLES, "dismiss items")
        reason = body.reason.strip()
        if not reason:
            raise InvalidError("A reason is required to dismiss items.")
        ids = list(dict.fromkeys(body.item_ids))
        items = [self._item(i) for i in ids]  # 404 before anything is changed
        for item in items:
            if item.state == ItemState.PUBLISHED:
                raise ConflictError(f"{item.internal_ref} is already in an approved update.")
        sources = {s.id: s for s in self._repo.list_sources()}
        decisions: list[ReviewDecision] = []
        for item in items:
            source = sources[item.source_id]
            prefill = self._prefill(item, source, None)
            decisions.append(
                ReviewDecision(
                    id=f"dec-{uuid.uuid4().hex[:10]}",
                    item_id=item.id,
                    reviewer=user.name,
                    decided_at=now_ist(),
                    treatment=Treatment.NOT_RELEVANT,
                    include_in_update=False,
                    dismiss_reason=reason,
                    ref_no_and_date=prefill.ref_no_and_date,
                    authority=prefill.authority,
                    guideline_title=prefill.guideline_title,
                    issue_date=prefill.issue_date,
                    publish_date=prefill.publish_date,
                )
            )
        self._repo.save_decisions(decisions, ItemState.DECIDED, user.name)
        return BulkDismissOut(dismissed=ids)

    # --- manual upload -----------------------------------------------------------------------

    def upload_document(
        self, user: AppUser, item_id: str, filename: str, content_type: str | None, data: bytes
    ) -> Document:
        require_role(user, REVIEW_ROLES, "upload a document")
        item = self._item(item_id)
        if item.state != ItemState.NEEDS_MANUAL_FETCH:
            raise ConflictError("Only items that need a manual fetch accept an uploaded document.")
        if not data:
            raise InvalidError("The uploaded file is empty.")
        if len(data) > MAX_UPLOAD_BYTES:
            raise TooLargeError("The file is larger than 20 MB.")
        name = (
            re.sub(r"[^\w.\- ]", "_", filename.replace("\\", "/").rsplit("/", 1)[-1]).strip()
            or "upload"
        )
        digest = hashlib.sha256(data).hexdigest()
        ext = ("." + name.rsplit(".", 1)[-1][:8]) if "." in name else ""
        doc = Document(
            id=f"doc-{uuid.uuid4().hex[:10]}",
            item_id=item_id,
            sha256=digest,
            path=f"data/documents/{item.internal_ref}/{digest}{ext}",
            mime=content_type,
            fetch_status=FetchStatus.MANUAL,
            uploaded_by=user.name,
            fetched_at=now_ist(),
            filename=name,
        )
        return self._repo.add_document(doc, data, ItemState.IN_REVIEW, user.name)
