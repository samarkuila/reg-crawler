"""Email renderer (stub for Prompt 5): HTML body from Jinja2 and an unsent `.eml` draft.

The system never sends email (CLAUDE.md rule 4). Nothing here talks to a mail server: it only
produces text that a person opens in Outlook and sends.
"""

from email.message import EmailMessage
from email.utils import formataddr
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.schemas.entities import EmailTemplate
from app.schemas.enums import StatusColour
from app.services.formatting import CYCLE_NAMES, fmt_date

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"

# Email legend (docs/ui-mockups/02-email-preview.html). Inline hex is required in email HTML.
STATUS_LEGEND: dict[StatusColour, tuple[str, str]] = {
    StatusColour.NOT_APPLICABLE: ("Not Applicable", "#808080"),
    StatusColour.ACTION_OVER: ("Action over", "#0B8A3E"),
    StatusColour.IN_PROGRESS: ("Action in Progress", "#1F5FD6"),
    StatusColour.NOT_AS_PER_PLAN: ("Progress not as per plan", "#D93025"),
    StatusColour.YET_TO_START: ("Action yet to start", "#F2B200"),
}


class EmailRenderService:
    def __init__(self) -> None:
        self._env = Environment(
            loader=FileSystemLoader(TEMPLATES_DIR / "email"),
            autoescape=select_autoescape(["html"]),
        )

    @staticmethod
    def subject(template: EmailTemplate, cycle_date: Any, cycle: Any) -> str:
        return template.subject_pattern.format(date=fmt_date(cycle_date), cycle=CYCLE_NAMES[cycle])

    def render_html(
        self,
        template: EmailTemplate,
        *,
        subject: str,
        cycle_date: Any,
        rows: list[dict[str, Any]],
        source_notes: list[str],
    ) -> str:
        return self._env.get_template("daily_update.html").render(
            subject=subject,
            greeting=template.greeting,
            intro=template.intro,
            disclaimer=template.disclaimer,
            no_update=not rows,
            no_update_text=template.no_update_text.format(date=fmt_date(cycle_date)),
            rows=rows,
            source_notes=source_notes,
            legend=[{"label": label, "hex": hex_} for label, hex_ in STATUS_LEGEND.values()],
            sign_off=template.sign_off,
            sender_name=template.sender_name,
        )

    @staticmethod
    def build_eml(
        *, subject: str, html: str, to: list[tuple[str, str]], cc: list[tuple[str, str]]
    ) -> bytes:
        """An unsent draft: `X-Unsent: 1` makes Outlook open it as a new message to send."""
        msg = EmailMessage()
        msg["Subject"] = subject
        msg["To"] = ", ".join(formataddr(p) for p in to)
        if cc:
            msg["Cc"] = ", ".join(formataddr(p) for p in cc)
        msg["X-Unsent"] = "1"
        msg.set_content("Open this draft in Outlook to see the formatted table.")
        msg.add_alternative(html, subtype="html")
        return msg.as_bytes()
