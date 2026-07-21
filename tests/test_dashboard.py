"""Tests for dashboard metrics and /dashboard/* endpoints."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from src.api.execution_events import ExecutionEventStore
from src.api.metrics_store import (
    METRICS_PREFIX,
    METRICS_TTL_SECONDS,
    aggregate_hag,
    aggregate_runs,
    aggregate_tokens,
    record_cursor_request,
    record_hag_decision,
    record_run_completed,
    record_run_started,
    record_token_usage,
    set_redis_client,
)
from src.api.redis_store import DictRedisClient, dump_job
from src.api.schemas import TicketJobStatus
from src.api.ticket_jobs import TicketJob, TicketJobStore
from src.core.token_usage import record_llm_response, start_pipeline_tracking, stop_pipeline_tracking


@pytest.fixture
def redis_client():
    return DictRedisClient()


@pytest.fixture
def metrics_redis(redis_client):
    set_redis_client(redis_client)
    yield redis_client
    set_redis_client(None)


@pytest.fixture
def event_store(redis_client):
    return ExecutionEventStore(redis_client=redis_client)


@pytest.fixture
def job_store(redis_client, event_store):
    return TicketJobStore(redis_client=redis_client, events=event_store)


@pytest.fixture
def dashboard_client(monkeypatch, job_store, event_store, metrics_redis):
    from dataclasses import replace

    from src.api import main as api_main
    from src.api import ticket_jobs as ticket_jobs_mod
    from src.core import config as config_mod

    patched_settings = replace(config_mod.settings, dashboard_api_key="test-dashboard-key")
    monkeypatch.setattr(config_mod, "settings", patched_settings)
    import src.api.dashboard as dashboard_mod

    monkeypatch.setattr(dashboard_mod, "settings", patched_settings)
    monkeypatch.setattr(ticket_jobs_mod, "ticket_job_store", job_store)
    monkeypatch.setattr(api_main, "ticket_job_store", job_store)
    monkeypatch.setattr(api_main, "event_store", event_store)
    with patch.object(job_store, "start_worker", new=AsyncMock()), patch.object(
        job_store, "stop_worker", new=AsyncMock()
    ):
        yield TestClient(api_main.app)


def test_metrics_incr_token_and_ttl(metrics_redis):
    """Redis counters increment and receive TTL on write."""
    today = datetime.now(timezone.utc).date().isoformat()
    record_token_usage("research", 150)
    record_token_usage("research", 50)
    record_cursor_request()

    total_key = f"{METRICS_PREFIX}:tokens:total:{today}"
    agent_key = f"{METRICS_PREFIX}:tokens:research:{today}"
    cursor_key = f"{METRICS_PREFIX}:cursor_requests:{today}"

    assert metrics_redis.get(total_key) == "200"
    assert metrics_redis.get(agent_key) == "200"
    assert metrics_redis.get(cursor_key) == "1"
    assert metrics_redis._ttl[total_key] == METRICS_TTL_SECONDS
    assert metrics_redis._ttl[cursor_key] == METRICS_TTL_SECONDS


def test_record_llm_response_writes_redis(metrics_redis):
    """record_llm_response persists tokens to Redis alongside in-memory tracker."""
    today = datetime.now(timezone.utc).date().isoformat()
    start_pipeline_tracking()
    try:
        record_llm_response(
            "planner",
            AIMessage(
                content="ok",
                response_metadata={
                    "token_usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 5,
                        "total_tokens": 15,
                    }
                },
            ),
        )
    finally:
        stop_pipeline_tracking()

    assert metrics_redis.get(f"{METRICS_PREFIX}:tokens:total:{today}") == "15"
    assert metrics_redis.get(f"{METRICS_PREFIX}:tokens:planner:{today}") == "15"


def test_hag_decision_allowlist(metrics_redis):
    """HAG increments only for allowlisted statuses."""
    today = datetime.now(timezone.utc).date().isoformat()
    record_hag_decision("RESEARCH", "APPROVED")
    record_hag_decision("RESEARCH", "REVISE")
    record_hag_decision("RESEARCH", "REJECT")
    record_hag_decision("PLANNING", "REJECTED")
    record_hag_decision("RESEARCH", "TIMEOUT")

    assert metrics_redis.get(f"{METRICS_PREFIX}:hag:research:approved:{today}") == "1"
    assert metrics_redis.get(f"{METRICS_PREFIX}:hag:research:revised:{today}") == "1"
    assert metrics_redis.get(f"{METRICS_PREFIX}:hag:research:rejected:{today}") == "1"
    assert metrics_redis.get(f"{METRICS_PREFIX}:hag:planning:rejected:{today}") == "1"
    assert metrics_redis.get(f"{METRICS_PREFIX}:hag:research:timeout:{today}") is None


def test_aggregate_tokens_week(metrics_redis):
    """Period aggregation sums daily keys across a week."""
    metrics_redis.incrby(f"{METRICS_PREFIX}:tokens:total:2026-07-21", 100)
    metrics_redis.incrby(f"{METRICS_PREFIX}:tokens:orchestrator:2026-07-21", 40)
    metrics_redis.incrby(f"{METRICS_PREFIX}:tokens:total:2026-07-22", 50)
    metrics_redis.incrby(f"{METRICS_PREFIX}:cursor_requests:2026-07-21", 3)

    from datetime import date

    result = aggregate_tokens("week", date.fromisoformat("2026-07-21"))
    assert result["total_tokens"] == 150
    assert result["by_agent"]["orchestrator"] == 40
    assert result["cursor_requests"] == 3
    assert result["start_date"] == "2026-07-20"  # Monday of week containing 2026-07-21
    assert result["end_date"] == "2026-07-26"


def test_aggregate_runs_success_rate(metrics_redis):
    """Success rate excludes cancelled runs (completed / (completed + failed))."""
    from datetime import date

    day = "2026-07-21"
    metrics_redis.incrby(f"{METRICS_PREFIX}:runs:started:{day}", 5)
    metrics_redis.incrby(f"{METRICS_PREFIX}:runs:completed:{day}", 3)
    metrics_redis.incrby(f"{METRICS_PREFIX}:runs:failed:{day}", 1)

    result = aggregate_runs("day", date.fromisoformat(day))
    assert result["started"] == 5
    assert result["completed"] == 3
    assert result["failed"] == 1
    assert result["success_rate"] == 0.75
    assert "Cancelled" in result["note"]


def test_aggregate_hag(metrics_redis):
    from datetime import date

    day = "2026-07-21"
    metrics_redis.incrby(f"{METRICS_PREFIX}:hag:research:approved:{day}", 2)
    metrics_redis.incrby(f"{METRICS_PREFIX}:hag:planning:revised:{day}", 1)

    result = aggregate_hag("day", date.fromisoformat(day))
    assert result["gates"]["research"]["approved"] == 2
    assert result["gates"]["planning"]["revised"] == 1
    assert result["gates"]["planning"]["approved"] == 0


def test_dashboard_auth_required(dashboard_client):
    """Dashboard routes return 401 without a valid API key."""
    for path in ("/dashboard/tokens", "/dashboard/runs", "/dashboard/hag", "/dashboard/health"):
        assert dashboard_client.get(path).status_code == 401
        assert (
            dashboard_client.get(path, headers={"X-API-Key": "wrong-key"}).status_code == 401
        )


def test_dashboard_tokens_endpoint(dashboard_client, metrics_redis):
    """GET /dashboard/tokens aggregates seeded Redis values."""
    today = datetime.now(timezone.utc).date().isoformat()
    metrics_redis.incrby(f"{METRICS_PREFIX}:tokens:total:{today}", 500)
    metrics_redis.incrby(f"{METRICS_PREFIX}:tokens:executor:{today}", 200)
    metrics_redis.incrby(f"{METRICS_PREFIX}:cursor_requests:{today}", 7)

    response = dashboard_client.get(
        "/dashboard/tokens",
        headers={"X-API-Key": "test-dashboard-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total_tokens"] == 500
    assert body["by_agent"]["executor"] == 200
    assert body["cursor_requests"] == 7


def test_dashboard_health_stuck_jobs(dashboard_client, job_store, metrics_redis):
    """GET /dashboard/health reports in-flight and stuck jobs."""
    from src.api.redis_store import job_key, ticket_active_key

    stale = datetime.now(timezone.utc) - timedelta(minutes=45)
    job = TicketJob(
        job_id="job-stuck-1",
        ticket_id="PLAT-99",
        status=TicketJobStatus.RUNNING,
        thread_id="thread-1",
        created_at=stale.isoformat(),
        updated_at=stale.isoformat(),
    )
    metrics_redis.set(job_key(job.job_id), dump_job(job.to_dict()))
    metrics_redis.set(ticket_active_key("PLAT-99"), job.job_id)

    response = dashboard_client.get(
        "/dashboard/health",
        headers={"X-API-Key": "test-dashboard-key"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["in_flight_count"] == 1
    assert body["stuck_count"] == 1
    assert body["stuck_jobs"][0]["job_id"] == "job-stuck-1"


def test_enqueue_increments_run_started(dashboard_client, metrics_redis):
    """POST /run increments runs:started via ticket_jobs hook."""
    today = datetime.now(timezone.utc).date().isoformat()
    response = dashboard_client.post(
        "/run",
        json={"ticket_id": "PLAT-100"},
        headers={"X-API-Key": "test-dashboard-key"},
    )
    assert response.status_code == 200
    assert metrics_redis.get(f"{METRICS_PREFIX}:runs:started:{today}") == "1"


def test_emit_lifecycle_increments_completed(metrics_redis, job_store):
    """execution_completed lifecycle event increments runs:completed."""
    today = datetime.now(timezone.utc).date().isoformat()
    set_redis_client(metrics_redis)
    job = TicketJob(
        job_id="job-done",
        ticket_id="PLAT-1",
        status=TicketJobStatus.RUNNING,
        thread_id="t1",
        created_at=datetime.now(timezone.utc).isoformat(),
        updated_at=datetime.now(timezone.utc).isoformat(),
    )
    job_store._emit_lifecycle(job, "execution_completed", {"ticket_id": "PLAT-1"})
    record_run_started()
    assert metrics_redis.get(f"{METRICS_PREFIX}:runs:completed:{today}") == "1"
