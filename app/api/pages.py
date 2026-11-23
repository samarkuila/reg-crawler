"""Page routes: Jinja2 shells only. Data is loaded by JS from /api/v1."""

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter(include_in_schema=False)


@router.get("/styleguide", response_class=HTMLResponse)
def styleguide(request: Request) -> HTMLResponse:
    """Living reference of every shared UI component (sample data only)."""
    return templates.TemplateResponse(
        request,
        "pages/styleguide.html",
        {
            "active_nav": None,
            "cycles": [
                {"id": "20-eod", "label": "20.11.2026 · End of day", "selected": True},
                {"id": "20-am", "label": "20.11.2026 · Morning", "selected": False},
            ],
            "current_user": "Divya G. · Reviewer",
        },
    )
