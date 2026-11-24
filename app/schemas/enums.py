"""Enums for every entity in architecture.md §4. Values are UPPER_CASE exactly as documented."""

from enum import StrEnum


class SourceType(StrEnum):
    RSS = "RSS"
    HTML_LIST = "HTML_LIST"
    STRUCTURED_DATA = "STRUCTURED_DATA"
    BROWSER = "BROWSER"


class Priority(StrEnum):
    NORMAL = "NORMAL"
    URGENT = "URGENT"


class BaselineStatus(StrEnum):
    NOT_BASELINED = "NOT_BASELINED"
    BASELINED = "BASELINED"


class ItemOrigin(StrEnum):
    BASELINE = "BASELINE"  # stored by a source's first crawl (the window)
    CRAWL = "CRAWL"  # found by a later crawl


class SeenReason(StrEnum):
    OUT_OF_WINDOW = "OUT_OF_WINDOW"
    UNDATED_BEYOND_LIMIT = "UNDATED_BEYOND_LIMIT"


class FirstRunMode(StrEnum):
    CURRENT_MONTH = "current_month"
    LAST_N_DAYS = "last_n_days"
    ALL = "all"


class VersionStatus(StrEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"


class Cycle(StrEnum):
    MORNING = "MORNING"
    EOD = "EOD"
    ADHOC = "ADHOC"
    URGENT = "URGENT"


class RunStatus(StrEnum):
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"


class CheckStatus(StrEnum):
    OK = "OK"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    PARSE_ERROR = "PARSE_ERROR"
    ZERO_ITEMS = "ZERO_ITEMS"


class Channel(StrEnum):
    """Item channel (RSS / HTML / BOTH). A source check is made on one channel only."""

    RSS = "RSS"
    HTML = "HTML"
    BOTH = "BOTH"


class CheckChannel(StrEnum):
    """Channel a single source check read. XML covers STRUCTURED_DATA sources such as UNSC."""

    RSS = "RSS"
    HTML = "HTML"
    XML = "XML"


class ChangeType(StrEnum):
    NEW = "NEW"
    MODIFIED = "MODIFIED"
    BACKDATED = "BACKDATED"
    REAPPEARED = "REAPPEARED"


class ItemState(StrEnum):
    DETECTED = "DETECTED"
    NEEDS_MANUAL_FETCH = "NEEDS_MANUAL_FETCH"
    ENRICHED = "ENRICHED"  # optional, set by the Luna agent at most (architecture §7a)
    IN_REVIEW = "IN_REVIEW"
    DECIDED = "DECIDED"
    PUBLISHED = "PUBLISHED"


class FetchStatus(StrEnum):
    OK = "OK"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    MANUAL = "MANUAL"


class Treatment(StrEnum):
    INFO_ONLY = "INFO_ONLY"
    OTHER_FUNCTION = "OTHER_FUNCTION"
    FUTURE_POTENTIAL = "FUTURE_POTENTIAL"
    IMPLEMENTATION_REQUIRED = "IMPLEMENTATION_REQUIRED"
    NOT_RELEVANT = "NOT_RELEVANT"


class Applicability(StrEnum):
    YES = "YES"
    NO = "NO"
    TBC = "TBC"


class StatusColour(StrEnum):
    NOT_APPLICABLE = "NOT_APPLICABLE"
    ACTION_OVER = "ACTION_OVER"
    IN_PROGRESS = "IN_PROGRESS"
    NOT_AS_PER_PLAN = "NOT_AS_PER_PLAN"
    YET_TO_START = "YET_TO_START"


class UpdateStatus(StrEnum):
    DRAFT = "DRAFT"
    APPROVED = "APPROVED"
    EXPORTED = "EXPORTED"


class UserRole(StrEnum):
    VIEWER = "viewer"
    REVIEWER = "reviewer"
    APPROVER = "approver"
    ADMIN = "admin"


class RecipientKind(StrEnum):
    TO = "TO"
    CC = "CC"


class AlertKind(StrEnum):
    """Why an alert exists. Stored for the UI banner now and Teams/email notifications in V2."""

    SOURCE_FAILED = "SOURCE_FAILED"
    SOURCE_BLOCKED = "SOURCE_BLOCKED"
    SOURCE_ZERO_ITEMS = "SOURCE_ZERO_ITEMS"
    SOURCE_PARSE_ERROR = "SOURCE_PARSE_ERROR"
    SANITY_WARNING = "SANITY_WARNING"
    URGENT_ITEM = "URGENT_ITEM"
    RUN_PROBLEM = "RUN_PROBLEM"  # the run itself had trouble (detection or documents failed)
    RUN_SKIPPED = "RUN_SKIPPED"  # a scheduled run could not start (another run was going)
    RUN_MISSED = "RUN_MISSED"  # a scheduled run did not happen; a catch-up was started


class NbfcRelevance(StrEnum):
    """Luna's reading of how much an item concerns NBFCs (agent spec T1)."""

    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class AgentTask(StrEnum):
    SCREEN = "SCREEN"
    EXTRACT = "EXTRACT"
    SWEEP = "SWEEP"


class AgentStatus(StrEnum):
    OK = "OK"
    INVALID_JSON = "INVALID_JSON"
    QUOTE_REJECTED = "QUOTE_REJECTED"  # a quote was not in the text, so that field was nulled
    ERROR = "ERROR"
    BUDGET = "BUDGET"  # the daily budget was used up; nothing was sent


class FlagType(StrEnum):
    POSSIBLE_MISS = "POSSIBLE_MISS"
    DISAGREEMENT = "DISAGREEMENT"


class FlagStatus(StrEnum):
    OPEN = "OPEN"
    RESOLVED = "RESOLVED"
    FALSE_ALARM = "FALSE_ALARM"
