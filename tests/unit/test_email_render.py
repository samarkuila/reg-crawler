"""Snapshot tests for the email renderer (no network, no database).

Each case renders fixed input and compares it with a file in tests/fixtures/email/.
After an intended change to the template, review the diff, then refresh the files with:
    $env:UPDATE_SNAPSHOTS = "1"; .\\tasks.ps1 test
"""

import os
from datetime import date
from email import message_from_bytes, policy
from pathlib import Path

from app.repositories.seed import email_template
from app.schemas.enums import Cycle
from app.services.email_render import EmailRenderService
from app.services.formatting import fmt_date

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "email"
CYCLE_DATE = date(2026, 11, 20)

IORS = {
    "serial_no": 1,
    "ref_no_and_date": "Inter-operable Regulatory Sandbox: IoRS – FAQs",
    "date_note": "Date: 19.11.2026 · Published on website: 20.11.2026",
    "authority": "RBI",
    "guideline_title": "Inter-operable Regulatory Sandbox (IoRS)",
    "link": "https://www.rbi.org.in/Scripts/FAQDisplay.aspx?Id=174",
    "link_text": "rbi.org.in/Scripts/FAQDisplay.aspx?Id=174",
    "applicability": "Yes",
    "effective_date": "19.11.2026",
    "status_hex": "#F2B200",
    "status_label": "Action yet to start",
    "impact_text": "The updated FAQs explain the Inter-operable Regulatory Sandbox.",
}
PIB = {
    "serial_no": 2,
    "ref_no_and_date": "PIB Release ID 2336151 dated 14.11.2026",
    "date_note": None,
    "authority": "MeitY",
    "guideline_title": "Consent Manager Registration Framework Comes into Effect",
    "link": "https://pib.gov.in/PressReleaseDetail.aspx?PRID=2336151",
    "link_text": "pib.gov.in/PressReleaseDetail.aspx?PRID=2336151",
    "applicability": "Yes",
    "effective_date": "13.11.2026",
    "status_hex": "#F2B200",
    "status_label": "Action yet to start",
    "impact_text": "The registration framework for Consent Managers is now in effect.",
}


def check(name: str, actual: str) -> None:
    path = FIXTURES / name
    if os.environ.get("UPDATE_SNAPSHOTS") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(actual, encoding="utf-8", newline="\n")
    assert path.read_text(encoding="utf-8") == actual, f"{name} changed; see the docstring above"


def render(rows: list[dict], notes: list[str]) -> str:
    renderer = EmailRenderService()
    template = email_template()
    subject = renderer.subject(template, CYCLE_DATE, Cycle.EOD)
    return renderer.render_html(
        template, subject=subject, cycle_date=CYCLE_DATE, rows=rows, source_notes=notes
    )


def test_subject_follows_the_pattern_setting() -> None:
    subject = EmailRenderService.subject(email_template(), CYCLE_DATE, Cycle.EOD)
    assert subject == "Daily Regulatory Update – 20.11.2026 (EOD)"


def test_normal_email_snapshot() -> None:
    html = render([IORS, PIB], [])
    check("normal.html", html)
    assert html.count("<th ") == 8
    assert "Date: 19.11.2026 · Published on website: 20.11.2026" in html  # FR-33
    assert "Regards,<br>Divya Gopakumar" in html and "font-style:italic" in html
    assert all(c in html for c in ("#808080", "#0B8A3E", "#1F5FD6", "#D93025", "#F2B200"))
    assert "<script" not in html and "http://" not in html.replace("href=", "")


def test_no_update_email_snapshot() -> None:
    html = render([], [])
    check("no_update.html", html)
    assert "There were no relevant regulatory updates for 20.11.2026." in html
    assert "<table" not in html


def test_failed_source_is_named_in_the_email() -> None:
    notes = [
        "eGazette checked manually by Divya G. at 19:52.",
        "TRAI could not be checked automatically (failed) and has not been checked manually.",
    ]
    html = render([PIB], notes)
    check("failed_source.html", html)
    assert all(n in html for n in notes)


def test_sme_head_is_copied_and_eml_is_an_unsent_draft() -> None:
    renderer = EmailRenderService()
    html = render([IORS], [])
    eml = renderer.build_eml(
        subject="Daily Regulatory Update – 20.11.2026 (EOD)",
        html=html,
        to=[("TVSCS Compliance team", "tvscs-compliance@example.invalid")],
        cc=[("Treasury head", "treasury-head@example.invalid")],
    )
    msg = message_from_bytes(eml, policy=policy.default)
    headers = "\n".join(f"{k}: {msg[k]}" for k in ("Subject", "To", "Cc", "X-Unsent"))
    check("sme_cc_headers.txt", headers + "\n")
    assert msg["X-Unsent"] == "1" and "Date" not in msg  # a draft: no send date, no sender
    assert msg.get_body(("html",)).get_content().strip() == html.strip()


def test_dates_use_dd_mm_yyyy() -> None:
    assert fmt_date(date(2026, 1, 5)) == "05.01.2026"
