"""Tests for ticket-based pipeline API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.redis_store import DictRedisClient
from src.api.schemas import TicketJobStatus
from src.api.ticket_jobs import TicketJobStore


@pytest.fixture
def redis_client():
    """In-memory Redis substitute."""
    return DictRedisClient()


@pytest.fixture
def job_store(redis_client):
    """Isolated job store for each test."""
    return TicketJobStore(redis_client=redis_client)


@pytest.fixture
def client(monkeypatch, job_store):
    """FastAPI test client with mocked worker lifecycle."""
    from src.api import main as api_main
    from src.api import ticket_jobs as ticket_jobs_mod

    monkeypatch.setattr(ticket_jobs_mod, "ticket_job_store", job_store)
    monkeypatch.setattr(api_main, "ticket_job_store", job_store)
    with patch.object(job_store, "start_worker", new=AsyncMock()), patch.object(
        job_store, "stop_worker", new=AsyncMock()
    ):
        yield TestClient(api_main.app)


def test_health(client):
    """GET /health returns ok."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_run_enqueues_ticket(client, job_store):
    """POST /run queues a pipeline job for the ticket id."""
    response = client.post(
        "/run",
        json={"ticket_id": "plat-1234", "user_prompt": "fix sqli"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"] == "PLAT-1234"
    assert body["status"] == "queued"
    assert body["job_id"]
    assert body["thread_id"]
    assert job_store.get_job(body["job_id"]) is not None


def test_run_rejects_empty_ticket_id(client):
    """POST /run rejects blank ticket_id."""
    response = client.post("/run", json={"ticket_id": "   "})
    assert response.status_code == 422


def test_run_conflict_when_ticket_active(client, job_store):
    """POST /run returns 409 when the ticket already has an active job."""
    job_store.enqueue("PLAT-55")
    response = client.post("/run", json={"ticket_id": "PLAT-55"})
    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["ticket_id"] == "PLAT-55"
    assert body["detail"]["status"] == TicketJobStatus.QUEUED.value


def test_ticket_status_not_found(client):
    """GET /tickets returns 404 for unknown ids."""
    response = client.get("/tickets/UNKNOWN-999")
    assert response.status_code == 404


def test_ticket_status_by_job_id(client, job_store):
    """GET /tickets resolves by job_id."""
    job = job_store.enqueue("PLAT-99")
    response = client.get(f"/tickets/{job.job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == TicketJobStatus.QUEUED.value


def test_ticket_status_by_ticket_id(client, job_store):
    """GET /tickets resolves by Jira ticket key."""
    job = job_store.enqueue("PLAT-77")
    response = client.get("/tickets/PLAT-77")
    assert response.status_code == 200
    assert response.json()["job_id"] == job.job_id


def test_run_reconciles_zombie_running_job(client, job_store):
    """POST /run succeeds when a stale RUNNING job is left from a crashed worker."""
    job = job_store.enqueue("PLAT-88")
    job.status = TicketJobStatus.RUNNING
    job_store._save_job(job)

    response = client.post("/run", json={"ticket_id": "PLAT-88"})
    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"] == "PLAT-88"
    assert body["job_id"] != job.job_id

    old = job_store.get_job(job.job_id)
    assert old is not None
    assert old.status == TicketJobStatus.FAILED


def test_approve_unknown_thread(client):
    """POST /approve returns 404 for unknown thread_id."""
    response = client.post(
        "/approve/missing-thread",
        json={"status": "APPROVE"},
    )
    assert response.status_code == 404
