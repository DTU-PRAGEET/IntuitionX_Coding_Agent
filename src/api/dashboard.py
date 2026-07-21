"""Dashboard observability endpoints — Redis-backed metrics aggregation."""

from __future__ import annotations

from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Query, status

from src.api.metrics_store import (
    Period,
    aggregate_hag,
    aggregate_runs,
    aggregate_tokens,
    collect_health,
    get_metrics_redis,
)
from src.core.config import settings

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def verify_dashboard_api_key(
    x_api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Require valid dashboard API key (fail closed when unset)."""
    expected = settings.dashboard_api_key
    if not expected or x_api_key != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing API key",
        )


def _parse_anchor(date_str: str | None) -> date | None:
    if date_str is None:
        return None
    try:
        return date.fromisoformat(date_str)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="date must be YYYY-MM-DD",
        ) from exc


@router.get("/tokens", dependencies=[Depends(verify_dashboard_api_key)])
def dashboard_tokens(
    period: Period = Query(default="day"),
    date: str | None = Query(default=None, description="Anchor date YYYY-MM-DD (UTC)"),
):
    """Token usage and Cursor request counts for the period."""
    return aggregate_tokens(period, _parse_anchor(date))


@router.get("/runs", dependencies=[Depends(verify_dashboard_api_key)])
def dashboard_runs(
    period: Period = Query(default="day"),
    date: str | None = Query(default=None, description="Anchor date YYYY-MM-DD (UTC)"),
):
    """Pipeline run started/completed/failed counts and success rate."""
    return aggregate_runs(period, _parse_anchor(date))


@router.get("/hag", dependencies=[Depends(verify_dashboard_api_key)])
def dashboard_hag(
    period: Period = Query(default="day"),
    date: str | None = Query(default=None, description="Anchor date YYYY-MM-DD (UTC)"),
):
    """HAG gate approval/rejection/revision counts for research and planning."""
    return aggregate_hag(period, _parse_anchor(date))


@router.get("/health", dependencies=[Depends(verify_dashboard_api_key)])
def dashboard_health():
    """In-flight jobs and jobs stuck longer than the configured threshold."""
    return collect_health(get_metrics_redis(), settings.dashboard_stuck_job_minutes)
