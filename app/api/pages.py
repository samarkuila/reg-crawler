"""Page routes: Jinja2 shells only. Data is loaded by JS from /api/v1.

Also hosts the POC user switcher (a cookie; shown only when APP_ENV is not "prod").
"""

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.api.deps import USER_COOKIE, get_services
from app.services import Services

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=TEMPLATES_DIR)

router = APIRouter(include_in_schema=False)

PAGES: dict[str, tuple[str, str, str]] = {
    # path: (nav key, title, lead)
    "/review": (
        "review",
        "Daily review",
        "What is new since the last cycle, and what needs a decision.",
    ),
    "/health": ("health", "Source health", "Every source, every run. No silent failures."),
    "/email": ("email", "Email preview", "The daily update, built only from reviewed items."),
    "/history": ("history", "Item history", "Search every item and see the full evidence chain."),
    "/sources": (
        "sources",
        "Sources",
        "What we monitor and how. Every change needs a second person's approval.",
    ),
    "/settings": ("settings", "Settings", "Run times, routing, recipients, wording and keywords."),
    "/audit": ("audit", "Audit log", "Who did what, and when. Read only."),
}


def switcher_enabled() -> bool:
    return os.environ.get("APP_ENV", "dev").strip().lower() != "prod"


def page_context(request: Request, services: Services, active_nav: str | None) -> dict[str, Any]:
    user = services.users.resolve(request.cookies.get(USER_COOKIE))
    cycles = services.review.list_cycles()
    wanted = request.query_params.get("cycle")
    selected = next((c.id for c in cycles if c.id == wanted), cycles[0].id if cycles else None)
    query = f"?{request.url.query}" if request.url.query else ""
    return {
        "active_nav": active_nav,
        "cycles": [{"id": c.id, "label": c.label, "selected": c.id == selected} for c in cycles],
        "current_user": f"{user.name} · {user.role.value.capitalize()}",
        "current_user_id": user.id,
        "users": services.users.list_users() if switcher_enabled() else None,
        "next_url": request.url.path + query,
    }


@router.get("/")
def index() -> RedirectResponse:
    return RedirectResponse("/review", status_code=307)


# Screens that have their own template (the rest use the generic "not built yet" shell).
PAGE_TEMPLATES: dict[str, str] = {
    "review": "pages/review.html",
    "email": "pages/email.html",
    "health": "pages/health.html",
    "history": "pages/history.html",
    "sources": "pages/sources.html",
    "settings": "pages/settings.html",
    "audit": "pages/audit.html",
}


def _make_page(path: str) -> None:
    nav, title, lead = PAGES[path]

    def view(request: Request, services: Services = Depends(get_services)) -> HTMLResponse:
        ctx = page_context(request, services, nav)
        ctx.update(title=title, lead=lead, page_key=nav)
        return templates.TemplateResponse(request, PAGE_TEMPLATES.get(nav, "pages/shell.html"), ctx)

    view.__name__ = f"page_{nav}"
    router.add_api_route(path, view, methods=["GET"], response_class=HTMLResponse)


for _path in PAGES:
    _make_page(_path)


@router.get("/styleguide", response_class=HTMLResponse)
def styleguide(request: Request, services: Services = Depends(get_services)) -> HTMLResponse:
    """Living reference of every shared UI component (sample data only)."""
    ctx = page_context(request, services, None)
    return templates.TemplateResponse(request, "pages/styleguide.html", ctx)


@router.post("/switch-user")
def switch_user(
    user: str = Form(...),
    next: str = Form("/review"),
    services: Services = Depends(get_services),
) -> RedirectResponse:
    """POC only: remember who is using the app. Not authentication."""
    if not switcher_enabled():
        raise HTTPException(status_code=404)
    if services.users.get(user) is None:
        raise HTTPException(status_code=422, detail="Unknown user")
    target = (
        next
        if next.startswith("/") and not next.startswith("//") and "\\" not in next
        else "/review"
    )
    response = RedirectResponse(target, status_code=303)
    response.set_cookie(USER_COOKIE, user, samesite="lax", httponly=True)
    return response
