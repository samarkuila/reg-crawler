"""API layer: routers validate input and call services. They never touch the repository's data."""

from fastapi import APIRouter

from app.api import audit, daily_update, items, runs, session, settings, sources

api_router = APIRouter(prefix="/api/v1")
for module in (session, runs, items, daily_update, sources, settings, audit):
    api_router.include_router(module.router)
