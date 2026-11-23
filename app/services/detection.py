"""Detection: what is new, changed, back-dated or back again (FR-10 … FR-13).

Reads the raw listing rows a crawl stored (`raw_listing_item`) and turns them into regulatory items
and observations. It is deterministic and uses no AI (policy A2). "New" is decided by fingerprint
only, never by the date a website shows (CLAUDE.md rule 6):

    fingerprint = sha256(source_id | normalised ref no.)   if there is a reference number
                = sha256(source_id | canonical url)        otherwise

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
from dataclasses import dataclass, field
from datetime import date

from app.repositories.base import Repository
from app.schemas.entities import (
    CrawlRun,
    ItemObservation,
    RawListingItem,
    RegulatoryItem,
    Source,
    SourceCheck,
    now_ist,
)
from app.schemas.enums import (
    ChangeType,
    Channel,
    CheckChannel,
    CheckStatus,
    ItemState,
    Priority,
)
from app.services import relevance
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


def content_hash(row: RawListingItem) -> str:
    """Hash of what a channel says about the item: title, reference number and any text."""
    text = html_to_text(str(row.raw_extra.get("description_html") or ""))
    parts = [re.sub(r"\s+", " ", row.title).strip(), normalise_ref_key(row.ref_no or ""), text]
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

    def count(self, change: ChangeType) -> int:
        return sum(1 for e in self.events if e.change == change)


class DetectionService:
    def __init__(self, repo: Repository) -> None:
        self._repo = repo

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

        now = now_ist()
        unchanged: list[str] = []
        for fp, per_channel in seen.items():
            channels = set(per_channel)
            row = per_channel[next(c for c in _CHANNEL_ORDER if c in per_channel)]
            existing = self._repo.find_item_by_fingerprint(fp)
            hashes = {c: content_hash(r) for c, r in per_channel.items()}

            if existing is None:
                change = self._new_or_backdated(row, previous)
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
                )
                result.events.append(event)
                continue

            prior = self._prior_hashes(existing.id)
            modified = any(c in prior and prior[c] != h for c, h in hashes.items())
            reappeared = previous is not None and fp not in previous_fps
            if modified or reappeared:
                change = ChangeType.MODIFIED if modified else ChangeType.REAPPEARED
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
                )
                result.events.append(event)
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
        self._repo.mark_items_seen(unchanged, now, ACTOR)
        result.warnings.extend(self._sanity(source, this_run, history))

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
    def _new_or_backdated(row: RawListingItem, previous: CrawlRun | None) -> ChangeType:
        if previous and row.displayed_date and row.displayed_date < previous.cycle_date:
            return ChangeType.BACKDATED
        return ChangeType.NEW

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
        self, run, source, fp, row, channels, change, hashes, per_channel, both_read, now
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
