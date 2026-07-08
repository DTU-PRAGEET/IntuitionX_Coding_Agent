"""Tests for ticket-based pipeline API endpoints."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.execution_events import ExecutionEventStore
from src.api.redis_store import DictRedisClient
from src.api.schemas import TicketJobStatus
from src.api.ticket_jobs import TicketJobStore


@pytest.fixture
def redis_client():
    """In-memory Redis substitute."""
    return DictRedisClient()


@pytest.fixture
def event_store(redis_client):
    """In-memory event store for tests."""
    return ExecutionEventStore(redis_client=redis_client)


@pytest.fixture
def job_store(redis_client, event_store):
    """Isolated job store for each test."""
    return TicketJobStore(redis_client=redis_client, events=event_store)


@pytest.fixture
def client(monkeypatch, job_store, event_store):
    """FastAPI test client with mocked worker lifecycle."""
    from src.api import main as api_main
    from src.api import ticket_jobs as ticket_jobs_mod

    monkeypatch.setattr(ticket_jobs_mod, "ticket_job_store", job_store)
    monkeypatch.setattr(api_main, "ticket_job_store", job_store)
    monkeypatch.setattr(api_main, "event_store", event_store)
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


def test_enqueue_emits_job_queued_event(client, job_store, event_store):
    """POST /run enqueues a job_queued lifecycle event."""
    response = client.post("/run", json={"ticket_id": "PLAT-EVT"})
    assert response.status_code == 200
    job_id = response.json()["job_id"]
    events = event_store.list_events(job_id).events
    assert len(events) == 1
    assert events[0].type == "job_queued"
    assert events[0].payload["ticket_id"] == "PLAT-EVT"


def test_list_events_not_found(client):
    """GET /jobs/{job_id}/events returns 404 for unknown job."""
    response = client.get("/jobs/nonexistent-job-id/events")
    assert response.status_code == 404


def test_list_events_after_enqueue(client, job_store, event_store):
    """GET /jobs/{job_id}/events returns queued event."""
    job = job_store.enqueue("PLAT-42")
    response = client.get(f"/jobs/{job.job_id}/events")
    assert response.status_code == 200
    body = response.json()
    assert body["job_id"] == job.job_id
    assert len(body["events"]) == 1
    assert body["events"][0]["type"] == "job_queued"
    assert body["latest_seq"] == 1


def test_ticket_status_includes_latest_event_seq(client, job_store):
    """GET /tickets includes latest_event_seq when events exist."""
    job = job_store.enqueue("PLAT-SEQ")
    response = client.get(f"/tickets/{job.job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["latest_event_seq"] == 1
    assert body.get("pause_context") is None


def test_ticket_status_pause_context_when_paused(client, job_store):
    """GET /tickets returns pause_context and display_message when job is paused."""
    job = job_store.enqueue("PLAT-PAUSE")
    job.status = TicketJobStatus.PAUSED
    job.paused_gate = "GATE_1"
    job.pause_context = {
        "gate": "GATE_1",
        "message": "--- APPROVAL REQUIRED: GATE 1 ---\nTicket: PLAT-PAUSE\nPlease respond with: APPROVE",
        "research_output": {"ticket_id": "PLAT-PAUSE"},
    }
    job_store._save_job(job)
    response = client.get(f"/tickets/{job.job_id}")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "paused"
    assert body["pause_context"]["gate"] == "GATE_1"
    assert "APPROVAL REQUIRED" in body["display_message"]
    assert body["pause_context"]["research_output"]["ticket_id"] == "PLAT-PAUSE"


def test_pause_display_plain_text(client, job_store):
    """GET /tickets/{id}/pause returns human-readable plain text."""
    job = job_store.enqueue("PLAT-TEXT")
    job.status = TicketJobStatus.PAUSED
    job.pause_context = {
        "gate": "GATE_1",
        "message": "--- APPROVAL REQUIRED: GATE 1 ---\nRoot Cause: hardcoded secret in Dockerfile",
    }
    job_store._save_job(job)
    response = client.get(f"/tickets/{job.job_id}/pause")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    text = response.text
    assert "APPROVAL REQUIRED" in text
    assert "hardcoded secret" in text
    assert job.thread_id in text
    assert "approve" in text.lower()


def test_pause_display_not_paused_returns_409(client, job_store):
    """GET /tickets/{id}/pause returns 409 when job is not paused."""
    job = job_store.enqueue("PLAT-RUN")
    response = client.get(f"/tickets/{job.job_id}/pause")
    assert response.status_code == 409


def test_sse_stream_delivers_events(client, job_store, event_store):
    """GET /jobs/{job_id}/events/stream yields SSE data lines."""
    job = job_store.enqueue("PLAT-SSE")
    event_store.append(job.job_id, job.thread_id, "execution_completed", {"ticket_id": "PLAT-SSE"})
    with client.stream("GET", f"/jobs/{job.job_id}/events/stream?after_seq=0") as response:
        assert response.status_code == 200
        assert "text/event-stream" in response.headers.get("content-type", "")
        chunks = []
        for chunk in response.iter_bytes():
            chunks.append(chunk)
            if b"execution_completed" in chunk:
                break
        text = b"".join(chunks).decode()
        assert "data: " in text
        assert "job_queued" in text or "execution_completed" in text


def test_cancel_paused_job_releases_ticket(client, job_store):
    """DELETE /tickets/{id} cancels a paused job and allows POST /run again."""
    job = job_store.enqueue("PLAT-CANCEL")
    job.status = TicketJobStatus.PAUSED
    job_store._save_job(job)

    response = client.delete("/tickets/PLAT-CANCEL")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "cancelled"
    assert body["ticket_id"] == "PLAT-CANCEL"
    assert job_store.get_active_for_ticket("PLAT-CANCEL") is None

    rerun = client.post("/run", json={"ticket_id": "PLAT-CANCEL"})
    assert rerun.status_code == 200
    assert rerun.json()["job_id"] != job.job_id


def test_cancel_queued_job_removes_from_queue(client, job_store, redis_client):
    """DELETE /tickets/{id} removes a queued job from the Redis work queue."""
    from src.api.redis_store import QUEUE_KEY

    job = job_store.enqueue("PLAT-QUEUE")
    assert job.job_id in (redis_client.lrange(QUEUE_KEY, 0, -1) or [])

    response = client.delete(f"/tickets/{job.job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == "cancelled"
    assert job.job_id not in (redis_client.lrange(QUEUE_KEY, 0, -1) or [])


def test_cancel_not_found(client):
    """DELETE /tickets/{id} returns 404 for unknown ids."""
    response = client.delete("/tickets/UNKNOWN-999")
    assert response.status_code == 404


def test_cancel_completed_returns_409(client, job_store):
    """DELETE /tickets/{id} returns 409 when job is already terminal."""
    job = job_store.enqueue("PLAT-DONE")
    job.status = TicketJobStatus.COMPLETED
    job_store._save_job(job)
    job_store._release_active(job)

    response = client.delete("/tickets/PLAT-DONE")
    assert response.status_code == 409
    assert response.json()["detail"]["status"] == "completed"


def test_cancel_running_in_flight_returns_409(client, job_store):
    """DELETE /tickets/{id} returns 409 while worker is actively executing."""
    job = job_store.enqueue("PLAT-BUSY")
    job.status = TicketJobStatus.RUNNING
    job_store._save_job(job)
    job_store._in_flight.add(job.job_id)

    response = client.delete("/tickets/PLAT-BUSY")
    assert response.status_code == 409
    assert "executing" in response.json()["detail"]["detail"].lower()


def test_cancel_emits_job_cancelled_event(client, job_store, event_store):
    """DELETE /tickets/{id} emits a job_cancelled lifecycle event."""
    job = job_store.enqueue("PLAT-CEVT")
    response = client.delete(f"/tickets/{job.job_id}")
    assert response.status_code == 200
    events = event_store.list_events(job.job_id).events
    assert any(e.type == "job_cancelled" for e in events)
