"""FastAPI application entry point."""

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response
from starlette.types import Scope

from app.api import api_router
from app.api.pages import router as pages_router
from app.services.errors import DomainError

BASE_DIR = Path(__file__).resolve().parent


class RevalidatedStaticFiles(StaticFiles):
    """Static files that browsers re-check on every load (a cheap 304 when nothing changed).

    Without this a browser may keep running an old script after an update.
    """

    async def get_response(self, path: str, scope: Scope) -> Response:
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Start the scheduler only when SCHEDULER_ENABLED=true (one worker; see .env.example)."""
    from app.api.deps import get_services

    scheduler = None
    if os.environ.get("SCHEDULER_ENABLED", "false").strip().lower() == "true":
        scheduler = get_services().scheduler
        scheduler.start()
    try:
        yield
    finally:
        if scheduler is not None:
            scheduler.shutdown()


app = FastAPI(
    lifespan=lifespan,
    title="Regulatory Monitor",
    version="0.1.0",
    description="TVS Credit compliance automation, Module 1 POC. Mock data unless REPO_BACKEND=sql",
)
app.mount("/static", RevalidatedStaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(api_router)
app.include_router(pages_router)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
