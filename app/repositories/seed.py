"""Mock data that reproduces docs/ui-mockups (sample data only; never used with REPO_BACKEND=sql).

Reference moment: the 20.11.2026 end-of-day run, with eGazette BLOCKED.
Placeholder contact addresses use the reserved `.invalid` domain.
"""

import hashlib
from datetime import date, datetime

from app.repositories.memory import MemoryRepository
from app.schemas.entities import (
    IST,
    AppUser,
    AuditEvent,
    CrawlRun,
    DailyUpdate,
    DailyUpdateItem,
    Document,
    EmailTemplate,
    ItemObservation,
    Recipient,
    RegulatoryItem,
    ReviewDecision,
    Setting,
    SmeFunction,
    Snapshot,
    Source,
    SourceCheck,
    SourceVersion,
)
from app.schemas.enums import (
    Applicability,
    ChangeType,
    Channel,
    CheckChannel,
    CheckStatus,
    Cycle,
    FetchStatus,
    ItemState,
    Priority,
    RecipientKind,
    RunStatus,
    SourceType,
    StatusColour,
    Treatment,
    UpdateStatus,
    UserRole,
    VersionStatus,
)

EOD_RUN = "run-2026-11-20-eod"


def ist(day: int, month: int, hour: int, minute: int, year: int = 2026) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=IST)


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def snapshot_bytes(snap_id: str, url: str) -> bytes:
    """Stored copy of a listing snapshot in the demo data (clearly marked as sample text)."""
    lines = [
        "SAMPLE SNAPSHOT (demo data, not real regulator text)",
        f"Source page: {url}",
        f"Snapshot: {snap_id}",
        "",
    ]
    return "\n".join(lines).encode()


IORS_DOCUMENT = (
    b"<html><body><h1>Inter-operable Regulatory Sandbox (IoRS) - FAQs</h1>"
    b"<p>SAMPLE DOCUMENT (demo data, not real regulator text).</p></body></html>"
)
IORS_DOCUMENT_SHA = hashlib.sha256(IORS_DOCUMENT).hexdigest()


def fingerprint(source_id: str, ref_no: str | None, canonical_url: str) -> str:
    return sha(f"{source_id}|{ref_no or canonical_url}")


# --- users -----------------------------------------------------------------------------------

USERS = [
    AppUser(id="u-divya", name="Divya G.", email="divya@example.invalid", role=UserRole.REVIEWER),
    AppUser(
        id="u-george", name="George K.", email="george@example.invalid", role=UserRole.APPROVER
    ),
    AppUser(
        id="u-samarendra",
        name="Samarendra K.",
        email="samarendra@example.invalid",
        role=UserRole.ADMIN,
    ),
    AppUser(id="u-audit", name="Audit", email="audit@example.invalid", role=UserRole.VIEWER),
]
DEFAULT_USER_ID = "u-divya"


# --- sources ---------------------------------------------------------------------------------

_RBI_TABS = [
    "Press releases",
    "Notifications",
    "Circulars",
    "Master Directions",
    "Master Circulars",
    "FAQs",
    "What's New",
]
_EGAZETTE_MINISTRIES_V2 = ["Finance", "MSME", "Communications", "Telecommunications"]
_EGAZETTE_MINISTRIES_V3 = [*_EGAZETTE_MINISTRIES_V2, "Electronics and IT", "Labour and Employment"]
_NBFC_KEYWORDS = ["NBFC", "non-banking", "regulated entities", "all lending institutions"]


def _source(
    id: str,
    authority: str,
    page: str,
    type_: SourceType,
    url: str,
    *,
    rss: str | None = None,
    priority: Priority = Priority.NORMAL,
    version: int = 1,
    adapter: dict | None = None,
    include: list[str] | None = None,
    active: bool = True,
) -> Source:
    return Source(
        id=id,
        authority=authority,
        page_name=page,
        type=type_,
        url=url,
        rss_url=rss,
        adapter_config=adapter or {},
        include_keywords=include or [],
        priority=priority,
        active=active,
        current_version=version if active else 0,
        planned=not active,
    )


def sources() -> list[Source]:
    poc = [
        _source(
            "rbi",
            "RBI",
            "All tabs (press releases, notifications, circulars, master directions)",
            SourceType.RSS,
            "https://www.rbi.org.in/",
            rss="https://rbi.org.in/notifications_rss.xml",
            version=2,
            adapter={"tabs": _RBI_TABS},
            include=_NBFC_KEYWORDS,
        ),
        _source(
            "unsc",
            "UNSC",
            "Consolidated sanctions list",
            SourceType.STRUCTURED_DATA,
            "https://scsanctions.un.org/resources/xml/en/consolidated.xml",
            priority=Priority.URGENT,
        ),
        _source(
            "trai",
            "TRAI",
            "Press releases",
            SourceType.HTML_LIST,
            "https://www.trai.gov.in/release-publication/press-releases",
        ),
        _source(
            "irdai",
            "IRDAI",
            "Circulars and guidelines",
            SourceType.HTML_LIST,
            "https://irdai.gov.in/circulars",
        ),
        _source(
            "egazette",
            "eGazette",
            "Extraordinary gazettes",
            SourceType.HTML_LIST,
            "https://egazette.gov.in/",
            version=2,
            adapter={"ministries": _EGAZETTE_MINISTRIES_V2},
        ),
    ]
    planned_defs = [
        ("npci", "NPCI", "NACH circulars", "https://www.npci.org.in/"),
        ("uidai", "UIDAI", "Circulars and notices", "https://uidai.gov.in/"),
        ("cersai", "CERSAI", "Notices", "https://www.cersai.org.in/"),
        ("ckyc", "CKYC", "Notices", "https://www.ckycindia.in/"),
        ("meity", "MeitY", "PIB press releases and notifications", "https://www.meity.gov.in/"),
        ("fiu-ind", "FIU-IND", "Circulars and guidelines", "https://fiuindia.gov.in/"),
        ("nfra", "NFRA", "Notices", "https://nfra.gov.in/"),
        ("mha", "MHA", "Sanctions and notifications", "https://www.mha.gov.in/"),
    ]
    planned = [
        _source(
            i,
            a,
            p,
            SourceType.HTML_LIST,
            u,
            active=False,
            adapter={"note": "To be confirmed by probe"},
        )
        for i, a, p, u in planned_defs
    ]
    return [*poc, *planned]


def source_versions() -> list[SourceVersion]:
    out: list[SourceVersion] = []
    for s in sources():
        if s.planned:
            continue
        for v in range(1, s.current_version + 1):
            out.append(
                SourceVersion(
                    id=f"srcv-{s.id}-{v}",
                    source_id=s.id,
                    version=v,
                    before={},
                    after={},
                    status=VersionStatus.APPROVED,
                    requested_by="Samarendra K.",
                    approved_by="George K.",
                    ts=ist(12, 11, 10, 0),
                    decided_at=ist(12, 11, 11, 0),
                )
            )
    out.append(
        SourceVersion(
            id="srcv-egazette-3",
            source_id="egazette",
            version=3,
            before={"adapter_config": {"ministries": _EGAZETTE_MINISTRIES_V2}},
            after={"adapter_config": {"ministries": _EGAZETTE_MINISTRIES_V3}},
            status=VersionStatus.PENDING,
            requested_by="Samarendra K.",
            ts=ist(21, 11, 11, 42),
        )
    )
    return out


# --- runs and checks -------------------------------------------------------------------------

_RUNS = [
    # key, date, cycle, start, end, status
    (
        "2026-11-03-eod",
        date(2026, 11, 3),
        Cycle.EOD,
        ist(3, 11, 19, 30),
        ist(3, 11, 19, 35),
        RunStatus.COMPLETED,
    ),
    (
        "2026-11-19-am",
        date(2026, 11, 19),
        Cycle.MORNING,
        ist(19, 11, 10, 0),
        ist(19, 11, 10, 5),
        RunStatus.COMPLETED,
    ),
    (
        "2026-11-19-eod",
        date(2026, 11, 19),
        Cycle.EOD,
        ist(19, 11, 19, 30),
        ist(19, 11, 19, 37),
        RunStatus.PARTIAL,
    ),
    (
        "2026-11-20-am",
        date(2026, 11, 20),
        Cycle.MORNING,
        ist(20, 11, 10, 0),
        ist(20, 11, 10, 5),
        RunStatus.COMPLETED,
    ),
    (
        "2026-11-20-eod",
        date(2026, 11, 20),
        Cycle.EOD,
        ist(20, 11, 19, 30),
        ist(20, 11, 19, 36),
        RunStatus.PARTIAL,
    ),
]

# (source_id, channel, items latest)
_CHECK_PLAN = [
    ("rbi", CheckChannel.RSS, 10),
    ("rbi", CheckChannel.HTML, 9),
    ("unsc", CheckChannel.XML, 1),
    ("trai", CheckChannel.HTML, 6),
    ("irdai", CheckChannel.HTML, 4),
    ("egazette", CheckChannel.HTML, None),
]
_SOURCE_URL = {s.id: s.url for s in sources()}
_SOURCE_RSS = {s.id: s.rss_url for s in sources()}


def runs() -> list[CrawlRun]:
    return [
        CrawlRun(
            id=f"run-{key}",
            cycle_date=d,
            cycle=c,
            triggered_by="scheduler",
            started_at=s,
            ended_at=e,
            status=st,
        )
        for key, d, c, s, e, st in _RUNS
    ]


def _check_status(
    run_key: str, source_id: str, channel: CheckChannel
) -> tuple[CheckStatus, str | None]:
    if run_key == "2026-11-20-eod" and source_id == "egazette":
        return (
            CheckStatus.BLOCKED,
            "Site showed a verification page. Not retried; manual check needed.",
        )
    if run_key == "2026-11-19-eod" and source_id == "irdai":
        return CheckStatus.FAILED, "Timeout after 30 s"
    if run_key == "2026-11-20-am" and source_id == "trai":
        return CheckStatus.ZERO_ITEMS, "Page read but no rows parsed; check selectors"
    return CheckStatus.OK, None


def checks_and_snapshots() -> tuple[list[SourceCheck], list[Snapshot]]:
    checks: list[SourceCheck] = []
    snaps: list[Snapshot] = []
    for key, d, _c, start, _e, _st in _RUNS:
        if key == "2026-11-03-eod":
            continue
        for source_id, channel, count in _CHECK_PLAN:
            status, detail = _check_status(key, source_id, channel)
            cid = f"chk-{key}-{source_id}-{channel.value.lower()}"
            snap_id: str | None = None
            if status in (CheckStatus.OK, CheckStatus.ZERO_ITEMS):
                snap_id = f"snap-{key}-{source_id}-{channel.value.lower()}"
                url = (
                    _SOURCE_RSS[source_id]
                    if channel == CheckChannel.RSS
                    else _SOURCE_URL[source_id]
                ) or ""
                digest = hashlib.sha256(snapshot_bytes(snap_id, url)).hexdigest()
                snaps.append(
                    Snapshot(
                        id=snap_id,
                        source_id=source_id,
                        channel=channel,
                        url=url,
                        fetched_at=start,
                        sha256=digest,
                        path=f"data/snapshots/{source_id}/{d.isoformat()}/{digest}",
                    )
                )
            found = (
                None
                if status in (CheckStatus.BLOCKED, CheckStatus.FAILED)
                else (0 if status == CheckStatus.ZERO_ITEMS else count)
            )
            checks.append(
                SourceCheck(
                    id=cid,
                    run_id=f"run-{key}",
                    source_id=source_id,
                    channel=channel,
                    status=status,
                    http_status=200 if status != CheckStatus.FAILED else None,
                    items_found=found,
                    error_detail=detail,
                    snapshot_id=snap_id,
                )
            )
    return checks, snaps


# --- items -----------------------------------------------------------------------------------


def _item(
    key: str,
    ref: str,
    source_id: str,
    title: str,
    url: str,
    *,
    ref_no: str | None = None,
    shown: date | None,
    channel: Channel,
    score: int,
    reason: str,
    state: ItemState,
    priority: Priority = Priority.NORMAL,
    mismatch: bool = False,
    summary: str | None = None,
    first_run: str = EOD_RUN,
    first_seen: datetime | None = None,
) -> RegulatoryItem:
    seen = first_seen or ist(20, 11, 19, 31)
    return RegulatoryItem(
        id=f"item-{key}",
        internal_ref=ref,
        source_id=source_id,
        fingerprint=fingerprint(source_id, ref_no, url),
        canonical_url=url,
        title=title,
        ref_no=ref_no,
        displayed_date=shown,
        channel=channel,
        channel_mismatch=mismatch,
        relevance_score=score,
        relevance_reason=reason,
        priority=priority,
        state=state,
        first_seen_run_id=first_run,
        first_seen_at=seen,
        last_seen_at=ist(20, 11, 19, 31),
        summary=summary,
    )


def items() -> list[RegulatoryItem]:
    return [
        _item(
            "unsc-amend",
            "TVSC-REG-2026-000220",
            "unsc",
            "UN sanctions list – one entry amended (alias added)",
            "https://scsanctions.un.org/resources/xml/en/consolidated.xml#amend-2026-11-19",
            shown=date(2026, 11, 19),
            channel=Channel.HTML,
            score=90,
            reason="Sanctions list change · screening list needs updating",
            state=ItemState.DETECTED,
            priority=Priority.URGENT,
            summary="AMENDED: one entry – alias added",
        ),
        _item(
            "iors-faqs",
            "TVSC-REG-2026-000214",
            "rbi",
            "Inter-operable Regulatory Sandbox: IoRS – FAQs",
            "https://rbi.org.in/Scripts/FAQDisplay.aspx?Id=174",
            shown=date(2026, 11, 17),
            channel=Channel.BOTH,
            score=82,
            reason="Mentions regulated entities · FAQ update",
            state=ItemState.IN_REVIEW,
            first_run="run-2026-11-03-eod",
            first_seen=ist(3, 11, 19, 31),
        ),
        _item(
            "rbi-backdated",
            "TVSC-REG-2026-000221",
            "rbi",
            "Notification on NBFC lending practices (sample)",
            "https://rbi.org.in/Scripts/NotificationUser.aspx?Id=sample-backdated",
            ref_no="RBI/2026-27/118",
            shown=date(2026, 11, 19),
            channel=Channel.BOTH,
            score=78,
            reason="Mentions NBFC · notification",
            state=ItemState.DETECTED,
        ),
        _item(
            "rbi-rss-only",
            "TVSC-REG-2026-000222",
            "rbi",
            "Draft circular for comments – NBFC disclosures (sample)",
            "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=sample-rss",
            shown=date(2026, 11, 20),
            channel=Channel.RSS,
            mismatch=True,
            score=58,
            reason="Mentions NBFC · draft for comments",
            state=ItemState.DETECTED,
        ),
        _item(
            "irdai-agents",
            "TVSC-REG-2026-000223",
            "irdai",
            "IRDAI circular on corporate agents (sample)",
            "https://irdai.gov.in/document-detail?documentId=sample-agents",
            shown=date(2026, 11, 20),
            channel=Channel.HTML,
            score=52,
            reason="Mentions corporate agent · route to Insurance team?",
            state=ItemState.DECIDED,
        ),
        _item(
            "rbi-pdf-blocked",
            "TVSC-REG-2026-000224",
            "rbi",
            "RBI press release PDF (sample)",
            "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=sample-pdf",
            shown=date(2026, 11, 20),
            channel=Channel.BOTH,
            score=60,
            reason="Press release; document not yet read",
            state=ItemState.NEEDS_MANUAL_FETCH,
        ),
        _item(
            "rbi-mmo",
            "TVSC-REG-2026-000225",
            "rbi",
            "Money market operations as on 20.11.2026",
            "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=sample-mmo",
            shown=date(2026, 11, 20),
            channel=Channel.BOTH,
            score=8,
            reason="Matches exclusion keyword: money market operations",
            state=ItemState.DETECTED,
        ),
        _item(
            "rbi-auction",
            "TVSC-REG-2026-000226",
            "rbi",
            "Auction result: treasury bills (sample)",
            "https://rbi.org.in/Scripts/BS_PressReleaseDisplay.aspx?prid=sample-auction",
            shown=date(2026, 11, 20),
            channel=Channel.BOTH,
            score=6,
            reason="Matches exclusion keyword: auction",
            state=ItemState.DETECTED,
        ),
        _item(
            "trai-subs",
            "TVSC-REG-2026-000227",
            "trai",
            "Telecom subscription data (sample)",
            "https://www.trai.gov.in/release-publication/press-releases/sample-subscription-data",
            shown=date(2026, 11, 20),
            channel=Channel.HTML,
            score=10,
            reason="No NBFC keywords · statistics release",
            state=ItemState.DETECTED,
        ),
        _item(
            "pib-2336151",
            "TVSC-REG-2026-000209",
            "meity",
            "Consent Manager Registration Framework Comes into Effect",
            "https://pib.gov.in/PressReleaseDetail.aspx?PRID=2336151",
            ref_no="PIB Release ID 2336151",
            shown=date(2026, 11, 14),
            channel=Channel.HTML,
            score=85,
            reason="DPDP Act framework · applies to data fiduciaries",
            state=ItemState.DECIDED,
        ),
    ]


_ITEM_CHANNEL = {
    "unsc-amend": CheckChannel.XML,
    "iors-faqs": CheckChannel.RSS,
    "rbi-rss-only": CheckChannel.RSS,
    "irdai-agents": CheckChannel.HTML,
    "trai-subs": CheckChannel.HTML,
    "pib-2336151": CheckChannel.HTML,
}
_ITEM_CHANGE = {"iors-faqs": ChangeType.MODIFIED, "rbi-backdated": ChangeType.BACKDATED}
# Channels each source is checked on (items from other sources, e.g. the planned MeitY row, have
# no snapshot in the mock run).
_CHECKED_CHANNELS: dict[str, set[CheckChannel]] = {}
for _sid, _ch, _n in _CHECK_PLAN:
    _CHECKED_CHANNELS.setdefault(_sid, set()).add(_ch)


def observations() -> list[ItemObservation]:
    out: list[ItemObservation] = []
    for it in items():
        key = it.id.removeprefix("item-")
        channel = _ITEM_CHANNEL.get(
            key, CheckChannel.HTML if it.channel != Channel.RSS else CheckChannel.RSS
        )
        change = _ITEM_CHANGE.get(key, ChangeType.NEW)
        cur = IORS_DOCUMENT_SHA if key == "iors-faqs" else sha(f"content-{key}-latest")
        snap = None
        if it.source_id in _CHECKED_CHANNELS:
            snap = f"snap-2026-11-20-eod-{it.source_id}-{channel.value.lower()}"
        if key == "iors-faqs":
            out.append(
                ItemObservation(
                    id=f"obs-{key}-first",
                    item_id=it.id,
                    run_id="run-2026-11-03-eod",
                    channel=CheckChannel.RSS,
                    change_type=ChangeType.NEW,
                    prior_hash=None,
                    current_hash=sha("c41d-iors"),
                    snapshot_id=None,
                )
            )
        out.append(
            ItemObservation(
                id=f"obs-{key}",
                item_id=it.id,
                run_id=EOD_RUN,
                channel=channel,
                change_type=change,
                prior_hash=sha("c41d-iors") if key == "iors-faqs" else None,
                current_hash=cur,
                snapshot_id=snap,
            )
        )
    return out


def documents() -> list[Document]:
    iors_sha = IORS_DOCUMENT_SHA
    return [
        Document(
            id="doc-iors-faqs",
            item_id="item-iors-faqs",
            url="https://rbi.org.in/Scripts/FAQDisplay.aspx?Id=174",
            sha256=iors_sha,
            path=f"data/documents/TVSC-REG-2026-000214/{iors_sha}.html",
            mime="text/html",
            text_path=f"data/documents/TVSC-REG-2026-000214/{iors_sha}.txt",
            fetch_status=FetchStatus.OK,
            fetched_at=ist(20, 11, 19, 31),
        ),
        Document(
            id="doc-pdf-blocked",
            item_id="item-rbi-pdf-blocked",
            url="https://rbi.org.in/Scripts/sample-press-release.pdf",
            fetch_status=FetchStatus.BLOCKED,
            fetched_at=ist(20, 11, 19, 32),
        ),
    ]


# --- decisions and the email draft ------------------------------------------------------------


def decisions() -> list[ReviewDecision]:
    return [
        ReviewDecision(
            id="dec-pib-2336151",
            item_id="item-pib-2336151",
            reviewer="Divya G.",
            decided_at=ist(20, 11, 19, 48),
            treatment=Treatment.INFO_ONLY,
            include_in_update=True,
            applicability=Applicability.YES,
            ref_no_and_date="PIB Release ID 2336151 dated 14.11.2026",
            authority="MeitY",
            guideline_title="Consent Manager Registration Framework Comes into Effect",
            issue_date=date(2026, 11, 14),
            publish_date=date(2026, 11, 14),
            effective_date=date(2026, 11, 13),
            status_colour=StatusColour.YET_TO_START,
            impact_text=(
                "The registration framework for Consent Managers under the DPDP Act, 2023 is now "
                "in effect. To be noted as part of the phased implementation of the DPDP framework."
            ),
            fields_edited={"effective_date": {"prefilled": "2026-11-14", "final": "2026-11-13"}},
        ),
        ReviewDecision(
            id="dec-irdai-agents",
            item_id="item-irdai-agents",
            reviewer="Divya G.",
            decided_at=ist(20, 11, 19, 50),
            treatment=Treatment.OTHER_FUNCTION,
            include_in_update=False,
            applicability=Applicability.TBC,
            authority="IRDAI",
            guideline_title="IRDAI circular on corporate agents (sample)",
            issue_date=date(2026, 11, 20),
            publish_date=date(2026, 11, 20),
            sme_function="insurance",
        ),
    ]


def daily_update() -> tuple[DailyUpdate, list[DailyUpdateItem]]:
    update = DailyUpdate(
        id="upd-2026-11-20-eod",
        cycle_date=date(2026, 11, 20),
        cycle=Cycle.EOD,
        status=UpdateStatus.DRAFT,
        subject="Daily Regulatory Update – 20.11.2026 (EOD)",
        prepared_by=["Divya G."],
    )
    rows = [
        DailyUpdateItem(
            update_id=update.id,
            item_id="item-pib-2336151",
            decision_id="dec-pib-2336151",
            serial_no=1,
        )
    ]
    return update, rows


# --- configuration ---------------------------------------------------------------------------


def sme_functions() -> list[SmeFunction]:
    names = [
        ("treasury", "Treasury"),
        ("finance", "Finance"),
        ("hr-admin", "HR / Admin"),
        ("aml-operations", "AML / Operations"),
        ("insurance", "Insurance"),
    ]
    return [
        SmeFunction(id=i, name=n, head_name=f"{n} head", head_email=f"{i}-head@example.invalid")
        for i, n in names
    ]


def recipients() -> list[Recipient]:
    return [
        Recipient(
            id="rcp-compliance",
            name="TVSCS Compliance team",
            email="tvscs-compliance@example.invalid",
            kind=RecipientKind.TO,
        ),
        Recipient(
            id="rcp-home-credit",
            name="Home Credit Compliance team",
            email="home-credit-compliance@example.invalid",
            kind=RecipientKind.TO,
        ),
    ]


def email_template() -> EmailTemplate:
    return EmailTemplate(
        id="tpl-daily-update",
        name="Daily regulatory update",
        subject_pattern="Daily Regulatory Update – {date} ({cycle})",
        greeting="Dear All,",
        intro=(
            "Appended below are the latest regulatory updates, the details of which are provided "
            "below. Please let us know if there are any additional relevant updates that should be "
            "included for our information and necessary action, wherever applicable."
        ),
        disclaimer=(
            "This update is based on our initial review of the referenced website. If any critical "
            "information impacting TVSCS has been missed, please provide the details for inclusion "
            "in the implementation tracker."
        ),
        no_update_text="There were no relevant regulatory updates for {date}.",
        sign_off="Regards,",
        sender_name="Divya Gopakumar",
    )


def settings() -> list[Setting]:
    at = ist(12, 11, 11, 0)

    def s(key: str, value: object, description: str) -> Setting:
        return Setting(
            key=key, value=value, description=description, updated_by="George K.", updated_at=at
        )

    return [
        s(
            "run-times",
            {
                "morning": "10:00",
                "eod": "19:30",
                "urgent_hourly_from": "08:00",
                "urgent_hourly_to": "20:00",
            },
            "Scheduled crawl times (IST)",
        ),
        s("working-days", ["MON", "TUE", "WED", "THU", "FRI"], "Days the scheduled runs happen"),
        s("holidays", [], "Dates with no scheduled run (DD.MM.YYYY)"),
        s("lookback-days", 2, "Days of listings each run re-reads"),
        s(
            "dismiss-reasons",
            [
                "Market operations – not NBFC",
                "Not applicable to TVS Credit",
                "Duplicate of an earlier item",
                "Statistics only",
            ],
            "Reasons offered when dismissing items",
        ),
        s(
            "exclusion-keywords",
            [
                "auction",
                "variable rate repo",
                "variable rate reverse repo",
                "money market operations",
                "maintenance",
                "downtime",
                "result",
                "marks",
                "vacancy",
                "recruitment",
                "tender",
                "bid invitation",
                "procurement",
                "citizenship",
                "premium statistics",
            ],
            "Ranking only: matches rank an item lower, never hide it",
        ),
        s("relevance-keywords", _NBFC_KEYWORDS, "Words that rank an item higher"),
        s("low-relevance-threshold", 30, "Items scoring below this are collapsed, never hidden"),
        s(
            "screening-guidance",
            {"text": ""},
            "Luna screening guidance. Owned and approved by Compliance",
        ),
    ]


def audit_history() -> list[AuditEvent]:
    def ev(
        i: int, ts: datetime, actor: str, action: str, entity: str, eid: str, after: object
    ) -> AuditEvent:
        return AuditEvent(
            id=f"aud-seed-{i}",
            ts=ts,
            actor=actor,
            action=action,
            entity=entity,
            entity_id=eid,
            before=None,
            after=after,
        )

    return [
        ev(
            1,
            ist(20, 11, 19, 30),
            "scheduler",
            "crawl_run.created",
            "crawl_run",
            EOD_RUN,
            {"cycle": "EOD", "cycle_date": "2026-11-20"},
        ),
        ev(
            2,
            ist(20, 11, 19, 36),
            "scheduler",
            "source_check.recorded",
            "source_check",
            "chk-2026-11-20-eod-egazette-html",
            {"status": "BLOCKED"},
        ),
        ev(
            3,
            ist(20, 11, 19, 48),
            "Divya G.",
            "review_decision.accepted",
            "review_decision",
            "dec-pib-2336151",
            {"item_id": "item-pib-2336151", "include_in_update": True},
        ),
        ev(
            4,
            ist(20, 11, 19, 50),
            "Divya G.",
            "review_decision.accepted",
            "review_decision",
            "dec-irdai-agents",
            {"item_id": "item-irdai-agents", "include_in_update": False},
        ),
        ev(
            5,
            ist(21, 11, 11, 42),
            "Samarendra K.",
            "source_version.requested",
            "source_version",
            "srcv-egazette-3",
            {"source_id": "egazette", "version": 3, "status": "PENDING"},
        ),
    ]


def load_seed(repo: MemoryRepository) -> MemoryRepository:
    checks, snaps = checks_and_snapshots()
    update, rows = daily_update()
    repo.load(USERS)
    repo.load(sources())
    repo.load(source_versions())
    repo.load(runs())
    repo.load(checks)
    repo.load(snaps)
    for snap in snaps:
        repo.load_blob(snap.sha256, snapshot_bytes(snap.id, snap.url))
    repo.load_blob(IORS_DOCUMENT_SHA, IORS_DOCUMENT)
    repo.load(items())
    repo.load(observations())
    repo.load(documents())
    repo.load(decisions())
    repo.load([update, *rows])
    repo.load(sme_functions())
    repo.load(recipients())
    repo.load([email_template()])
    repo.load(settings())
    repo.load(audit_history())
    return repo
