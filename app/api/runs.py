"""Runs, crawl start and source-check endpoints. Validation only; logic is in services."""

from datetime import date

from fastapi import APIRouter, Depends, Query

from app.api.deps import current_user, get_services
from app.schemas.api import (
    CrawlRunIn,
    ManualCheckIn,
    RunDetail,
    SourceHealthSummary,
)
from app.schemas.entities import Alert, AppUser, CrawlRun, ManualCheck
from app.services import Services

router = APIRouter(tags=["runs"])


@router.get("/runs", response_model=list[CrawlRun], summary="List crawl runs")
def list_runs(
    date_: date | None = Query(None, alias="date", description="Cycle date, YYYY-MM-DD"),
    services: Services = Depends(get_services),
) -> list[CrawlRun]:
    return services.review.list_runs(date_)


@router.get("/runs/{run_id}", response_model=RunDetail, summary="Run detail with per-source checks")
def get_run(run_id: str, services: Services = Depends(get_services)) -> RunDetail:
    return services.review.run_detail(run_id)


@router.post(
    "/crawl/run",
    response_model=CrawlRun,
    status_code=202,
    summary="Run now: crawl every active source, or one (409 if a run is already going)",
)
def start_crawl(
    body: CrawlRunIn | None = None,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> CrawlRun:
    """Starts the run in the background and returns it at once; follow it with GET /runs/{id}."""
    return services.crawl.start_for(user, body.source_id if body else None)


@router.get(
    "/alerts",
    response_model=list[Alert],
    summary="Alerts of a run (default: the latest run), urgent first",
)
def list_alerts(
    run_id: str | None = Query(None),
    services: Services = Depends(get_services),
) -> list[Alert]:
    return services.alerts.latest(run_id)


@router.get(
    "/source-checks",
    response_model=SourceHealthSummary,
    summary="Source health for a cycle (defaults to the latest cycle)",
)
def source_checks(
    cycle: str | None = Query(None, description="Cycle id, e.g. 2026-11-20-EOD"),
    services: Services = Depends(get_services),
) -> SourceHealthSummary:
    return services.review.source_health(cycle)


@router.post(
    "/source-checks/{check_id}/manual-check",
    response_model=ManualCheck,
    status_code=201,
    summary="Mark a failed or blocked source as checked manually",
)
def manual_check(
    check_id: str,
    body: ManualCheckIn,
    user: AppUser = Depends(current_user),
    services: Services = Depends(get_services),
) -> ManualCheck:
    return services.review.manual_check(user, check_id, body.note)
