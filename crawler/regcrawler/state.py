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

    def parse_error(self, channel: CheckChannel, detail: str) -> None:
        state = self.state(channel)
        if state.status is None:
            state.status = CheckStatus.PARSE_ERROR
            state.error_detail = detail
