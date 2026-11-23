"""Source health grid (FR-26): last four cycles per source and channel, compare and log views."""

from app.repositories.base import Repository
from app.schemas.api import (
    CheckLog,
    CompareItem,
    CompareResult,
    CycleRef,
    GridAction,
    GridCell,
    GridRow,
    ItemFilter,
    SourceGrid,
)
from app.schemas.entities import SourceCheck
from app.schemas.enums import Channel, CheckChannel, CheckStatus
from app.services.errors import NotFoundError
from app.services.formatting import CYCLE_NAMES, fmt_time
from app.services.review import STATUS_LABELS, ReviewService
from app.services.stored_copy import StoredFile, load_verified

_CHANNEL_ORDER = {CheckChannel.RSS: 0, CheckChannel.HTML: 1, CheckChannel.XML: 2}
_PROBLEM_TONE = {
    CheckStatus.FAILED: "urg",
    CheckStatus.BLOCKED: "urg",
    CheckStatus.PARSE_ERROR: "urg",
    CheckStatus.ZERO_ITEMS: "grey",
}
_SEEN_IN = {Channel.BOTH: "Both", Channel.RSS: "RSS only", Channel.HTML: "HTML only"}


def short_label(ref: CycleRef) -> str:
    return f"{ref.cycle_date.strftime('%d.%m')} {CYCLE_NAMES[ref.cycle]}"


class HealthService:
    def __init__(self, repo: Repository, review: ReviewService) -> None:
        self._repo = repo
        self._review = review

    # --- grid --------------------------------------------------------------------------------

    def _mismatches(self, ref: CycleRef) -> dict[tuple[str, CheckChannel], int]:
        """Items seen on only one channel, counted against the channel that missed them."""
        out: dict[tuple[str, CheckChannel], int] = {}
        for item in self._repo.list_items(ItemFilter(cycle=ref)):
            if not item.channel_mismatch:
                continue
            missed = CheckChannel.HTML if item.channel == Channel.RSS else CheckChannel.RSS
            key = (item.source_id, missed)
            out[key] = out.get(key, 0) + 1
        return out

    def _cell(self, ref: CycleRef, check: SourceCheck | None, mismatches: int) -> GridCell:
        if check is None:
            return GridCell(cycle=ref.id, check_id=None, label="–", tone="grey", status=None)
        if check.status == CheckStatus.OK:
            label, tone = (f"{mismatches} mismatch", "mod") if mismatches else ("OK", "ok")
            return GridCell(
                cycle=ref.id, check_id=check.id, label=label, tone=tone, status=check.status
            )
        label = STATUS_LABELS[check.status]
        if check.status == CheckStatus.FAILED and "timeout" in (check.error_detail or "").lower():
            label += " (timeout)"
        tone = _PROBLEM_TONE[check.status]
        if self._repo.get_manual_check(check.id):
            label += " · checked manually"
            tone = "mod"
        return GridCell(
            cycle=ref.id,
            check_id=check.id,
            label=label,
            tone=tone,  # type: ignore[arg-type]
            status=check.status,
        )

    def grid(self) -> SourceGrid:
        cycles = list(reversed(self._repo.list_cycles()[:4]))  # oldest to newest, left to right
        checks: dict[tuple[str, CheckChannel, str], SourceCheck] = {}
        for c in cycles:
            for run in reversed(self._repo.find_runs(c)):  # the newest run of a cycle wins
                for chk in self._repo.list_source_checks(run.id):
                    checks[(chk.source_id, chk.channel, c.id)] = chk
        mismatches = {c.id: self._mismatches(c) for c in cycles}
        rows: list[GridRow] = []
        for source in (s for s in self._repo.list_sources() if s.active):
            channels = sorted(
                {ch for (sid, ch, _c) in checks if sid == source.id}, key=_CHANNEL_ORDER.__getitem__
            )
            for channel in channels:
                cells = [
                    self._cell(
                        c,
                        checks.get((source.id, channel, c.id)),
                        mismatches[c.id].get((source.id, channel), 0),
                    )
                    for c in cycles
                ]
                latest = checks.get((source.id, channel, cycles[-1].id)) if cycles else None
                rows.append(
                    self._row(source.id, source.authority, source.page_name, channel, cells, latest)
                )
        runs = self._repo.list_runs()
        return SourceGrid(
            cycles=cycles,
            cycle_labels=[short_label(c) for c in cycles],
            rows=rows,
            current_run=runs[0] if runs else None,
        )

    def _row(  # noqa: PLR0913
        self,
        source_id: str,
        authority: str,
        page_name: str,
        channel: CheckChannel,
        cells: list[GridCell],
        latest: SourceCheck | None,
    ) -> GridRow:
        if latest is None or latest.items_found is None:
            items = "—"
        elif channel == CheckChannel.XML:
            items = f"{latest.items_found} change{'s' if latest.items_found != 1 else ''}"
        else:
            items = str(latest.items_found)
        needs = (
            latest is not None
            and latest.status != CheckStatus.OK
            and self._repo.get_manual_check(latest.id) is None
        )
        actions: list[GridAction] = []
        if latest is not None:
            if needs:
                actions.append(
                    GridAction(kind="manual", label="Mark checked manually", check_id=latest.id)
                )
                actions.append(GridAction(kind="log", label="View log", check_id=latest.id))
            elif latest.status in (
                CheckStatus.FAILED,
                CheckStatus.BLOCKED,
                CheckStatus.PARSE_ERROR,
            ):
                actions.append(GridAction(kind="log", label="View log", check_id=latest.id))
            elif "mismatch" in cells[-1].label:
                actions.append(
                    GridAction(kind="compare", label="Compare RSS vs HTML", check_id=latest.id)
                )
            elif latest.snapshot_id:
                actions.append(
                    GridAction(
                        kind="snapshot", label="View snapshot", snapshot_id=latest.snapshot_id
                    )
                )
        return GridRow(
            source_id=source_id,
            authority=authority,
            page_name=page_name,
            channel=channel.value,
            cells=cells,
            items_latest=items,
            needs_attention=needs,
            actions=actions,
        )

    # --- compare, log, snapshot --------------------------------------------------------------

    def compare(self, source_id: str, cycle: str | None) -> CompareResult:
        source = self._repo.get_source(source_id)
        if source is None:
            raise NotFoundError(f"Source {source_id} not found")
        ref = self._review.resolve_cycle(cycle)
        if ref is None:
            raise NotFoundError("No crawl has run yet.")
        in_cycle = self._repo.list_items(ItemFilter(cycle=ref))
        found = [i for i in in_cycle if i.source_id == source_id]
        rows = [
            CompareItem(
                title=i.title,
                ref_no=i.ref_no,
                displayed_date=i.displayed_date,
                seen_in=_SEEN_IN[i.channel],  # type: ignore[arg-type]
            )
            for i in found
        ]
        rows.sort(key=lambda r: (r.seen_in == "Both", r.title.lower()))
        n = {k: sum(1 for r in rows if r.seen_in == k) for k in ("Both", "RSS only", "HTML only")}
        if not source.rss_url:
            message = "This source has no RSS feed, so there is nothing to compare."
        elif n["RSS only"] or n["HTML only"]:
            message = "Some items were seen on one channel only. Both are shown to the reviewer."
        else:
            message = "RSS and HTML agree for this cycle."
        return CompareResult(
            source_id=source_id,
            cycle=ref.id,
            authority=source.authority,
            page_name=source.page_name,
            both=n["Both"],
            rss_only=n["RSS only"],
            html_only=n["HTML only"],
            items=rows,
            message=message,
        )

    def check_log(self, check_id: str) -> CheckLog:
        check = self._repo.get_source_check(check_id)
        if check is None:
            raise NotFoundError(f"Source check {check_id} not found")
        view = self._review.check_view(check)
        run = self._repo.get_run(check.run_id)
        label = short_label(CycleRef(cycle_date=run.cycle_date, cycle=run.cycle)) if run else ""
        lines = [f"Page: {view.authority} · {view.page_name} · {view.channel}"]
        if run:
            ended = f", ended {fmt_time(run.ended_at)}" if run.ended_at else ""
            lines.append(f"Run: {label}, started {fmt_time(run.started_at)}{ended}")
        lines.append(f"Result: {view.status_label}")
        if check.http_status is not None:
            lines.append(f"HTTP status: {check.http_status}")
        if check.items_found is not None:
            lines.append(f"Items found: {check.items_found}")
        if check.error_detail:
            lines.append(f"Detail: {check.error_detail}")
        if check.status == CheckStatus.BLOCKED:
            lines.append("The system never bypasses a verification page. It was not retried.")
        if view.manual_check:
            m = view.manual_check
            lines.append(f"Checked manually by {m.user} at {fmt_time(m.ts)}: {m.note}")
        elif check.status != CheckStatus.OK:
            lines.append("Not yet checked manually.")
        return CheckLog(
            check=view,
            cycle_label=label,
            run_started_at=run.started_at if run else None,
            run_ended_at=run.ended_at if run else None,
            lines=lines,
        )

    def snapshot_file(self, snapshot_id: str) -> StoredFile:
        snap = self._repo.get_snapshot(snapshot_id)
        if snap is None:
            raise NotFoundError(f"Snapshot {snapshot_id} not found")
        return load_verified(self._repo, snap.sha256, f"snapshot-{snap.id}.txt", None)
