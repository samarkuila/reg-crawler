"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api import api_router
from app.api.pages import router as pages_router
from app.services.errors import DomainError

BASE_DIR = Path(__file__).resolve().parent

app = FastAPI(
    title="Regulatory Monitor",
    version="0.1.0",
    description="TVS Credit compliance automation, Module 1 POC. Mock data unless REPO_BACKEND=sql",
)
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")
app.include_router(api_router)
app.include_router(pages_router)


@app.exception_handler(DomainError)
async def domain_error_handler(_: Request, exc: DomainError) -> JSONResponse:
    return JSONResponse({"detail": exc.message}, status_code=exc.status_code)
