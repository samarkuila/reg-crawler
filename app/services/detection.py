"""Detection: what is new, changed, back-dated or back again (FR-10 … FR-13).

Reads the raw listing rows a crawl stored (`raw_listing_item`) and turns them into regulatory items
and observations. It is deterministic and uses no AI (policy A2). "New" is decided by fingerprint
only, never by the date a website shows (CLAUDE.md rule 6):

    fingerprint = sha256(source_id | normalised ref no.)   if there is a reference number
                = sha256(source_id | canonical url)        otherwise

First crawl of a source (NOT_BASELINED): only items inside the first-run window are stored
(origin BASELINE); older rows are recorded as seen fingerprints only. The source is marked
BASELINED only when that run was healthy. Later crawls (BASELINED) compare every row with the
stored items and the seen fingerprints. See app/services/first_run.py.

Change types (architecture §4):
  NEW        fingerprint never seen
  BACKDATED  never seen, and its displayed date is before the date of the last healthy run
  MODIFIED   seen before and its title/text hash changed on a channel
  REAPPEARED seen before, absent from the previous healthy run, present now

Nothing is ever hidden or removed here. Items that did not change only get a new last-seen time.
"""

import hashlib
import re
import statistics
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

from app.repositories.base import Repository
from app.schemas.entities import (
    CrawlRun,
    ItemObservation,
    RawListingItem,
    RegulatoryItem,
    SeenFingerprint,
    Source,
    SourceCheck,
    now_ist,
)
from app.schemas.enums import (
    BaselineStatus,
    ChangeType,
    Channel,
    CheckChannel,
    CheckStatus,
    ItemOrigin,
    ItemState,
    Priority,
    SeenReason,
)
from app.services import relevance
from app.services.first_run import FirstRunConfig, Window, load_first_run_config, window_for
from app.services.normalise import html_to_text

ACTOR = "detection"
HEALTHY = (CheckStatus.OK, CheckStatus.ZERO_ITEMS)  # the channel answered, so absence means absence
MEDIAN_WINDOW = 7  # runs
DROP_RATIO = 0.5  # fewer than half the usual items is suspicious
MIN_HISTORY = 3  # runs needed before a drop is called a drop
_CHANNEL_ORDER = (CheckChannel.RSS, CheckChannel.HTML, CheckChannel.XML)
_REDECIDE = (ItemState.DECIDED, ItemState.PUBLISHED)


def normalise_ref_key(ref_no: str) -> str:
    return re.sub(r"\s+", "", ref_no).upper()


def fingerprint(source_id: str, ref_no: str | None, canonical_url: str) -> str:
    key = normalise_ref_key(ref_no) if ref_no and ref_no.strip() else canonical_url
    return hashlib.sha256(f"{source_id}|{key}".encode()).hexdigest()


def content_hash(row: RawListingItem, full: bool = True) -> str:
    """Hash of what a channel says about the item.

    The legacy hash (full=False) is title, reference number and any text. The full hash also
    covers the link, the displayed date and the attachment list, so a change to any of them is a
    MODIFIED item. Observations stored before this change hold the legacy hash, which still
    counts as "unchanged", so an upgrade does not flag every existing item.
    """
    text = html_to_text(str(row.raw_extra.get("description_html") or ""))
    parts = [re.sub(r"\s+", " ", row.title).strip(), normalise_ref_key(row.ref_no or ""), text]
    if full:
        attachments = sorted(str(a) for a in row.raw_extra.get("attachments") or [])
        parts += [
            row.url,
            row.displayed_date.isoformat() if row.displayed_date else "",
            *attachments,
        ]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


@dataclass
class DetectedEvent:
    item: RegulatoryItem
    change: ChangeType
    row: RawListingItem  # the row the item details came from (first channel in RSS, HTML, XML)
    channels: set[CheckChannel]
    is_new: bool


@dataclass
class SanityWarning:
    source_id: str
    channel: CheckChannel
    kind: str  # "ZERO_ITEMS" | "DROP"
    detail: str


@dataclass
class DetectionResult:
    run_id: str
    events: list[DetectedEvent] = field(default_factory=list)
    unchanged: int = 0
    warnings: list[SanityWarning] = field(default_factory=list)
    baselined: list[str] = field(default_factory=list)  # sources marked BASELINED by this run

    def count(self, change: ChangeType) -> int:
        return sum(1 for e in self.events if e.change == change)


class DetectionService:
    def __init__(
        self,
        repo: Repository,
        first_run: FirstRunConfig | None = None,
        today: Callable[[], date] | None = None,
    ) -> None:
        self._repo = repo
        self._first_run = first_run
        self._today = today  # injectable for tests

    def _config(self) -> FirstRunConfig:
        return self._first_run or load_first_run_config()

    # --- one run -----------------------------------------------------------------------------

    def process(self, run_id: str) -> DetectionResult:
        run = self._repo.get_run(run_id)
        if run is None:
            raise KeyError(f"Unknown crawl run {run_id}")
        result = DetectionResult(run_id)
        checks = self._repo.list_source_checks()
        by_source: dict[str, list[SourceCheck]] = defaultdict(list)
        for check in checks:
            by_source[check.source_id].append(check)
        for source_id, source_checks in by_source.items():
            this_run = [c for c in source_checks if c.run_id == run_id]
            source = self._repo.get_source(source_id)
            if not this_run or source is None:
                continue
            self._process_source(run, source, this_run, source_checks, result)
        return result

    def _process_source(
        self,
        run: CrawlRun,
        source: Source,
        this_run: list[SourceCheck],
        history: list[SourceCheck],
        result: DetectionResult,
    ) -> None:
        healthy = {c.channel for c in this_run if c.status in HEALTHY}
        both_channels_read = {CheckChannel.RSS, CheckChannel.HTML} <= healthy
        previous = self._previous_run(run, source.id, history)
        previous_fps = (
            {self._fp(source.id, r) for r in self._repo.list_raw_items(previous.id, source.id)}
            if previous
            else set()
        )

        seen: dict[str, dict[CheckChannel, RawListingItem]] = {}
        for row in self._repo.list_raw_items(run.id, source.id):
            seen.setdefault(self._fp(source.id, row), {}).setdefault(row.channel, row)

        baseline = source.baseline_status == BaselineStatus.NOT_BASELINED
        window: Window | None = None
        if baseline:
            window = window_for(source, self._config(), self._today() if self._today else None)
        known = {r.fingerprint for r in self._repo.list_seen_fingerprints(source.id)}
        undated_left = self._config().undated_max_items
        previous_raw = (
            {self._fp(source.id, r): r for r in self._repo.list_raw_items(previous.id, source.id)}
            if previous
            else {}
        )
        counts = {
            "rows_parsed": len(seen),
            "new": 0,
            "modified": 0,
            "backdated": 0,
            "reappeared": 0,
            "unchanged": 0,
            "known_older": 0,
            "baseline_items": 0,
            "seen_only": 0,  # first crawl: rows older than the window, kept as fingerprints
        }
        to_remember: list[SeenFingerprint] = []

        now = now_ist()
        unchanged: list[str] = []
        for fp, per_channel in seen.items():
            channels = set(per_channel)
            row = per_channel[next(c for c in _CHANNEL_ORDER if c in per_channel)]
            existing = self._repo.find_item_by_fingerprint(fp)
            hashes = {c: content_hash(r) for c, r in per_channel.items()}

            if existing is None and fp in known:
                counts["known_older"] += 1  # seen by the first crawl, older than its window
                continue

            if existing is None:
                origin, flags = ItemOrigin.CRAWL, []
                if baseline:
                    assert window is not None  # noqa: S101
                    out = self._out_of_window(row, window)
                    if out is None and row.displayed_date is None:
                        if undated_left <= 0:
                            out = SeenReason.UNDATED_BEYOND_LIMIT
                        undated_left -= 1
                    if out is not None:
                        to_remember.append(self._seen_row(run, source, fp, row, now, out))
                        counts["seen_only"] += 1
                        known.add(fp)
                        continue
                    origin, change = ItemOrigin.BASELINE, ChangeType.NEW
                else:
                    change, flags = self._new_or_backdated(row, previous, source)
                event = self._save_new(
                    run,
                    source,
                    fp,
                    row,
                    channels,
                    change,
                    hashes,
                    per_channel,
                    both_channels_read,
                    now,
                    origin,
                    flags,
                )
                result.events.append(event)
                if origin == ItemOrigin.BASELINE:
                    counts["baseline_items"] += 1
                else:
                    counts["backdated" if change == ChangeType.BACKDATED else "new"] += 1
                continue

            prior = self._prior_hashes(existing.id)
            modified = any(
                c in prior and prior[c] != h and prior[c] != content_hash(per_channel[c], False)
                for c, h in hashes.items()
            )
            reappeared = previous is not None and fp not in previous_fps
            if modified or reappeared:
                change = ChangeType.MODIFIED if modified else ChangeType.REAPPEARED
                reason = (
                    self._modified_reason(existing, row, previous_raw.get(fp)) if modified else None
                )
                event = self._save_existing(
                    run,
                    source,
                    existing,
                    row,
                    channels,
                    change,
                    hashes,
                    per_channel,
                    prior,
                    both_channels_read,
                    now,
                    reason,
                )
                result.events.append(event)
                counts["modified" if modified else "reappeared"] += 1
                continue

            # Nothing changed. If this run settles a channel mismatch, record that; else just
            # note that the item is still on the site.
            if existing.channel_mismatch and both_channels_read and len(channels) == 2:
                self._repo.save_detection(
                    existing.model_copy(
                        update={
                            "channel": Channel.BOTH,
                            "channel_mismatch": False,
                            "last_seen_at": now,
                        }
                    ),
                    [],
                    False,
                    ACTOR,
                )
            else:
                unchanged.append(existing.id)
            result.unchanged += 1
            counts["unchanged"] += 1
        self._repo.mark_items_seen(unchanged, now, ACTOR)
        if to_remember:
            self._repo.add_seen_fingerprints(to_remember, ACTOR)
        result.warnings.extend(self._sanity(source, this_run, history))
        self._finish_source(run, source, this_run, counts, window, baseline, result)

    # --- first crawl: window and baseline ----------------------------------------------------

    @staticmethod
    def _out_of_window(row: RawListingItem, window: Window) -> SeenReason | None:
        if row.displayed_date is not None and window.start and row.displayed_date < window.start:
            return SeenReason.OUT_OF_WINDOW
        return None

    @staticmethod
    def _seen_row(run, source, fp, row, now, reason) -> SeenFingerprint:  # type: ignore[no-untyped-def]
        return SeenFingerprint(
            id=f"seen-{uuid.uuid4().hex[:10]}",
            source_id=source.id,
            fingerprint=fp,
            title=row.title,
            published_date=row.displayed_date,
            first_seen_run_id=run.id,
            first_seen_at=now,
            reason=reason,
        )

    def _finish_source(  # noqa: PLR0913
        self,
        run: CrawlRun,
        source: Source,
        this_run: list[SourceCheck],
        counts: dict[str, int],
        window: Window | None,
        baseline: bool,
        result: DetectionResult,
    ) -> None:
        """Write the run counters, and mark the source BASELINED only after a healthy first run."""
        out_of_window = counts["seen_only"]
        stats: dict = dict(counts)  # type: ignore[type-arg]
        if baseline:  # on later crawls the crawler's own window_start (the lookback) stays
            stats["window_start"] = window.start.isoformat() if window and window.start else None
        problem: str | None = None
        if baseline:
            if any(c.status != CheckStatus.OK for c in this_run):
                problem = "a channel was blocked, failed, empty or could not be read"
            elif not counts["rows_parsed"]:
                problem = "no rows were parsed"
        fresh = [c for c in self._repo.list_source_checks(run.id) if c.source_id == source.id]
        for check in fresh:  # re-read: the sanity step may have added a warning note
            note = None
            if baseline and problem:
                note = f"Not baselined: {problem}; the next crawl applies the window again"
            detail = check.error_detail
            if note and (not detail or note not in detail):
                detail = f"{detail}; {note}" if detail else note
            merged = {**check.stats, **stats}
            if merged != check.stats or detail != check.error_detail:  # re-running changes nothing
                self._repo.record_source_check(
                    check.model_copy(update={"stats": merged, "error_detail": detail}), ACTOR
                )
        if baseline and problem is None:
            self._repo.set_source_baseline(
                source.id,
                BaselineStatus.BASELINED,
                window.start if window else None,
                ACTOR,
                "BASELINE_COMPLETED",
                {
                    "run_id": run.id,
                    "window_start": stats["window_start"],
                    "mode": window.mode.value if window else None,
                    "items_stored": counts["baseline_items"],
                    "seen_only": out_of_window,
                    "rows_parsed": counts["rows_parsed"],
                },
            )
            result.baselined.append(source.id)

    # --- helpers -----------------------------------------------------------------------------

    @staticmethod
    def _fp(source_id: str, row: RawListingItem) -> str:
        return fingerprint(source_id, row.ref_no, row.url)

    def _previous_run(
        self, run: CrawlRun, source_id: str, history: list[SourceCheck]
    ) -> CrawlRun | None:
        """The latest earlier run in which this source answered on at least one channel."""
        best: CrawlRun | None = None
        for check in history:
            if check.run_id == run.id or check.status not in HEALTHY:
                continue
            other = self._repo.get_run(check.run_id)
            if other is None or other.started_at >= run.started_at:
                continue
            if best is None or other.started_at > best.started_at:
                best = other
        return best

    @staticmethod
    def _new_or_backdated(
        row: RawListingItem, previous: CrawlRun | None, source: Source
    ) -> tuple[ChangeType, list[str]]:
        """NEW, or BACKDATED when the date shown is older than the baseline window (the link is
        new on the page although its date is old) or older than the last healthy run."""
        day = row.displayed_date
        if day and source.baseline_window_start and day < source.baseline_window_start:
            return ChangeType.BACKDATED, ["old date, new on page"]
        if previous and day and day < previous.cycle_date:
            return ChangeType.BACKDATED, ["old date, new on page"]
        return ChangeType.NEW, []

    @staticmethod
    def _modified_reason(
        existing: RegulatoryItem, row: RawListingItem, before: RawListingItem | None
    ) -> str:
        reasons = []
        if existing.title.strip() != row.title.strip():
            reasons.append("title changed")
        if row.displayed_date and existing.displayed_date != row.displayed_date:
            reasons.append("date changed")
        if before is not None:
            added = set(row.raw_extra.get("attachments") or []) - set(
                before.raw_extra.get("attachments") or []
            )
            if added:
                reasons.append("attachment added")
        return "; ".join(reasons) or "content changed"

    def _prior_hashes(self, item_id: str) -> dict[CheckChannel, str]:
        """The last known content hash on each channel (observations are in time order)."""
        out: dict[CheckChannel, str] = {}
        for obs in self._repo.list_observations(item_id):
            out[obs.channel] = obs.current_hash
        return out

    def _keywords(self, source: Source) -> tuple[list[str], list[str]]:
        def words(key: str) -> list[str]:
            setting = self._repo.get_setting(key)
            return [str(w) for w in setting.value] if setting and setting.value else []

        return (
            [*words("relevance-keywords"), *source.include_keywords],
            [*words("exclusion-keywords"), *source.exclude_keywords],
        )

    def _score(self, source: Source, row: RawListingItem) -> relevance.Relevance:
        include, exclude = self._keywords(source)
        text = html_to_text(str(row.raw_extra.get("description_html") or ""))
        return relevance.score(row.title, include, exclude, text)

    @staticmethod
    def _priority(source: Source, row: RawListingItem) -> Priority:
        urgent = source.priority == Priority.URGENT or row.raw_extra.get("priority") == "URGENT"
        return Priority.URGENT if urgent else Priority.NORMAL

    @staticmethod
    def _channel(channels: set[CheckChannel], current: Channel | None = None) -> Channel:
        names = {c.value for c in channels}
        if current is not None:
            names |= {"RSS", "HTML"} if current == Channel.BOTH else {current.value}
        if {"RSS", "HTML"} <= names:
            return Channel.BOTH
        return Channel.RSS if "RSS" in names else Channel.HTML  # XML-only sources read as HTML

    @staticmethod
    def _observations(
        item_id: str,
        run: CrawlRun,
        change: ChangeType,
        per_channel: dict[CheckChannel, RawListingItem],
        hashes: dict[CheckChannel, str],
        prior: dict[CheckChannel, str],
    ) -> list[ItemObservation]:
        return [
            ItemObservation(
                id=f"obs-{uuid.uuid4().hex[:10]}",
                item_id=item_id,
                run_id=run.id,
                channel=channel,
                change_type=change,
                prior_hash=prior.get(channel),
                current_hash=hashes[channel],
                snapshot_id=row.snapshot_id,
            )
            for channel, row in per_channel.items()
        ]

    def _save_new(
        self,
        run,
        source,
        fp,
        row,
        channels,
        change,
        hashes,
        per_channel,
        both_read,
        now,
        origin=ItemOrigin.CRAWL,
        flags=None,
    ) -> DetectedEvent:  # type: ignore[no-untyped-def]
        scored = self._score(source, row)
        item = RegulatoryItem(
            id=f"item-{uuid.uuid4().hex[:10]}",
            internal_ref="",  # the repository assigns TVSC-REG-YYYY-NNNNNN
            source_id=source.id,
            fingerprint=fp,
            canonical_url=row.url,
            title=row.title,
            ref_no=row.ref_no,
            displayed_date=row.displayed_date,
            channel=self._channel(channels),
            channel_mismatch=both_read and len(channels) == 1,
            relevance_score=scored.percent,
            relevance_reason=scored.reason,
            priority=self._priority(source, row),
            state=ItemState.DETECTED,
            first_seen_run_id=run.id,
            first_seen_at=now,
            last_seen_at=now,
            summary=row.raw_extra.get("summary"),
            origin=origin,
            flags=list(flags or []),
        )
        saved = self._repo.save_detection(
            item, self._observations(item.id, run, change, per_channel, hashes, {}), True, ACTOR
        )
        return DetectedEvent(saved, change, row, channels, True)

    def _save_existing(
        self,
        run,
        source,
        existing,
        row,
        channels,
        change,
        hashes,
        per_channel,
        prior,
        both_read,
        now,
        reason=None,
    ) -> DetectedEvent:  # type: ignore[no-untyped-def]
        update: dict = {  # type: ignore[type-arg]
            "title": row.title,
            "canonical_url": row.url,
            "ref_no": row.ref_no or existing.ref_no,
            "displayed_date": row.displayed_date or existing.displayed_date,
            "channel": self._channel(channels, existing.channel),
            "last_seen_at": now,
            "priority": (
                Priority.URGENT
                if Priority.URGENT in (existing.priority, self._priority(source, row))
                else Priority.NORMAL
            ),
            "summary": row.raw_extra.get("summary") or existing.summary,
        }
        if reason:
            update["summary"] = f"Changed: {reason}"
        if both_read:
            update["channel_mismatch"] = len(channels) == 1
        if change == ChangeType.MODIFIED:
            scored = self._score(source, row)
            update["relevance_score"] = scored.percent
            update["relevance_reason"] = scored.reason
            if existing.state in _REDECIDE:
                update["state"] = ItemState.DETECTED  # changed after review: it needs a new look
        item = existing.model_copy(update=update)
        saved = self._repo.save_detection(
            item, self._observations(item.id, run, change, per_channel, hashes, prior), False, ACTOR
        )
        return DetectedEvent(saved, change, row, channels, False)

    # --- sanity checks (FR-13) ---------------------------------------------------------------

    def _sanity(
        self, source: Source, this_run: list[SourceCheck], history: list[SourceCheck]
    ) -> list[SanityWarning]:
        warnings: list[SanityWarning] = []
        for check in this_run:
            usual = self._usual_count(check, history)
            warning: SanityWarning | None = None
            if check.status == CheckStatus.ZERO_ITEMS:
                tail = f"; it usually returns about {usual:g}" if usual else ""
                warning = SanityWarning(
                    source.id,
                    check.channel,
                    "ZERO_ITEMS",
                    f"{source.page_name} ({check.channel.value}) returned no items{tail}",
                )
            elif (
                check.status == CheckStatus.OK
                and check.channel != CheckChannel.XML  # a diff source: few changes is normal
                and check.items_found is not None
                and usual
                and check.items_found < DROP_RATIO * usual
            ):
                warning = SanityWarning(
                    source.id,
                    check.channel,
                    "DROP",
                    f"{source.page_name} ({check.channel.value}) returned {check.items_found} "
                    f"items; the usual is about {usual:g} (median of the last runs). "
                    "The page may have changed.",
                )
            if warning:
                warnings.append(warning)
                self._note(check, warning)
        return warnings

    def _usual_count(self, check: SourceCheck, history: list[SourceCheck]) -> float | None:
        """Median items found in the last 7 earlier healthy runs; None with too little history."""
        runs = {}
        for other in history:
            if (
                other.channel != check.channel
                or other.run_id == check.run_id
                or other.status != CheckStatus.OK
                or other.items_found is None
            ):
                continue
            started = self._repo.get_run(other.run_id)
            this = self._repo.get_run(check.run_id)
            if started and this and started.started_at < this.started_at:
                runs[other.run_id] = (started.started_at, other.items_found)
        recent = [n for _t, n in sorted(runs.values(), reverse=True)[:MEDIAN_WINDOW]]
        return float(statistics.median(recent)) if len(recent) >= MIN_HISTORY else None

    def _note(self, check: SourceCheck, warning: SanityWarning) -> None:
        text = f"Warning: {warning.detail}"
        if check.error_detail and text in check.error_detail:
            return
        detail = f"{check.error_detail}; {text}" if check.error_detail else text
        self._repo.record_source_check(check.model_copy(update={"error_detail": detail}), ACTOR)


def lookback_date(run: CrawlRun, days: int = 2) -> date:
    """FR-14: listings are re-read for the previous `days` days (the crawl uses this window)."""
    from datetime import timedelta

    return run.cycle_date - timedelta(days=days)
