"""API layer: routers validate input and call services. They never touch the repository's data."""

from fastapi import APIRouter

from app.api import (
    agent,
    audit,
    daily_update,
    history,
    items,
    runs,
    session,
    settings,
    sources,
)

api_router = APIRouter(prefix="/api/v1")
for module in (session, runs, items, agent, daily_update, history, sources, settings, audit):
    api_router.include_router(module.router)
