"""What happened to each channel of one source during one crawl (kept in memory until the DbPipeline
writes the source_check rows)."""

from dataclasses import dataclass

from app.schemas.enums import CheckChannel, CheckStatus


@dataclass
class ChannelState:
    channel: CheckChannel
    status: CheckStatus | None = None  # set only when the channel failed or was blocked
    http_status: int | None = None
    error_detail: str | None = None
    snapshot_id: str | None = None
    items_found: int = 0
    attempted: bool = False
    empty_is_ok: bool = False  # a diff source: "nothing changed" is a normal result, not ZERO_ITEMS


class CheckTracker:
    def __init__(self, expected: tuple[CheckChannel, ...] = ()) -> None:
        self.expected = expected
        self.channels: dict[CheckChannel, ChannelState] = {}
        self.blocked = False  # once True, no further request for this source is sent

    def state(self, channel: CheckChannel) -> ChannelState:
        return self.channels.setdefault(channel, ChannelState(channel))

    def mark_attempted(self, channel: CheckChannel, http_status: int | None = None) -> None:
        state = self.state(channel)
        state.attempted = True
        if http_status is not None:
            state.http_status = http_status

    def block(self, channel: CheckChannel, reason: str, http_status: int | None = None) -> None:
        state = self.state(channel)
        state.attempted = True
        state.status = CheckStatus.BLOCKED
        state.error_detail = reason
        state.http_status = http_status
        self.blocked = True

    def fail(self, channel: CheckChannel, detail: str, http_status: int | None = None) -> None:
        state = self.state(channel)
        state.attempted = True
        if state.status is None:  # a block is never downgraded to a plain failure
            state.status = CheckStatus.FAILED
            state.error_detail = detail
            state.http_status = http_status

    def note(self, channel: CheckChannel, text: str) -> None:
        """A remark kept on an otherwise healthy check (for example "baseline stored")."""
        state = self.state(channel)
        if state.status is None:
            state.error_detail = f"{state.error_detail}; {text}" if state.error_detail else text

    def parse_error(self, channel: CheckChannel, detail: str) -> None:
        state = self.state(channel)
        if state.status is None:
            state.status = CheckStatus.PARSE_ERROR
            state.error_detail = detail


@dataclass(frozen=True)
class Outcome:
    status: CheckStatus
    detail: str | None
    count: int | None
    http_status: int | None


def outcome(tracker: CheckTracker, channel: CheckChannel, found: int, skipped: int = 0) -> Outcome:
    """What the source check for `channel` should say. Used by the DbPipeline and by dry runs."""
    state = tracker.state(channel)
    status, detail, count = state.status, state.error_detail, found
    if status is None:
        if tracker.blocked and not state.attempted:
            status, detail = CheckStatus.BLOCKED, "Not fetched: the source was blocked earlier"
        elif not state.attempted:
            status, detail = (
                CheckStatus.FAILED,
                "No page was fetched (robots.txt disallowed it, or the site did not answer)",
            )
        elif found == 0 and not state.empty_is_ok:
            status = CheckStatus.ZERO_ITEMS
        else:
            status = CheckStatus.OK
    if status in (CheckStatus.BLOCKED, CheckStatus.FAILED) and found == 0:
        count = None  # nothing was read, so "0 found" would be misleading
    if skipped:
        note = f"{skipped} row(s) without a URL were skipped"
        detail = f"{detail}; {note}" if detail else note
    return Outcome(status, detail, count, state.http_status)
