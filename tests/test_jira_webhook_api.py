"""Tests for Jira webhook API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from src.api.redis_store import DictRedisClient
from src.api.schemas import TicketJobStatus
from src.api.ticket_jobs import TicketJobStore


def _labeled_issue(key: str, labels: list[str] | None = None) -> dict:
    """Labeled Issue for this module."""
    return {
        "webhookEvent": "jira:issue_created",
        "issue": {
            "key": key,
            "fields": {"labels": labels or ["auto-fix"]},
        },
    }


@pytest.fixture
def redis_client():
    """Redis Client for this module."""
    return DictRedisClient()


@pytest.fixture
def job_store(redis_client):
    """Job Store for this module."""
    return TicketJobStore(redis_client=redis_client)


@pytest.fixture
def client(monkeypatch, job_store):
    """Client for this module."""
    monkeypatch.setenv("WEBHOOK_API_KEY", "test-secret")
    monkeypatch.setenv("WEBHOOK_REQUIRED_LABELS", "auto-fix")
    from src.core import config
    from src.api import main as api_main
    from src.api import ticket_jobs as ticket_jobs_mod

    test_settings = config.Settings(
        webhook_api_key="test-secret",
        webhook_required_labels_raw="auto-fix",
    )
    monkeypatch.setattr(config, "settings", test_settings)
    monkeypatch.setattr(ticket_jobs_mod, "ticket_job_store", job_store)
    monkeypatch.setattr(api_main, "ticket_job_store", job_store)
    with patch.object(job_store, "start_worker", new=AsyncMock()), patch.object(
        job_store, "stop_worker", new=AsyncMock()
    ):
        yield TestClient(api_main.app)


def test_health(client):
    """Test Health for this module."""
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_requires_auth(client):
    """Test Webhook Requires Auth for this module."""
    response = client.post("/jira/webhook", json=_labeled_issue("PLAT-1"))
    assert response.status_code == 401


def test_webhook_rejects_invalid_key(client):
    """Test Webhook Rejects Invalid Key for this module."""
    response = client.post(
        "/jira/webhook",
        json=_labeled_issue("PLAT-1"),
        headers={"Authorization": "Bearer wrong"},
    )
    assert response.status_code == 401


def test_webhook_enqueues_ticket(client, job_store):
    """Test Webhook Enqueues Ticket for this module."""
    response = client.post(
        "/jira/webhook",
        json=_labeled_issue("plat-1234"),
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["ticket_id"] == "PLAT-1234"
    assert body["status"] == "queued"
    assert body["job_id"]
    assert job_store.get_job(body["job_id"]) is not None


def test_webhook_ignores_without_label(client):
    """Test Webhook Ignores Without Label for this module."""
    response = client.post(
        "/jira/webhook",
        json={
            "webhookEvent": "jira:issue_created",
            "issue": {"key": "PLAT-2", "fields": {"labels": []}},
        },
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ignored"


def test_webhook_conflict_when_ticket_active(client, job_store):
    """Test Webhook Conflict When Ticket Active for this module."""
    job_store.enqueue("PLAT-55")
    response = client.post(
        "/jira/webhook",
        json=_labeled_issue("PLAT-55"),
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 409
    body = response.json()
    assert body["detail"]["ticket_id"] == "PLAT-55"
    assert body["detail"]["status"] == TicketJobStatus.QUEUED.value


def test_webhook_unsupported_event(client):
    """Test Webhook Unsupported Event for this module."""
    response = client.post(
        "/jira/webhook",
        json={
            "webhookEvent": "jira:issue_deleted",
            "issue": {"key": "PLAT-1", "fields": {"labels": ["auto-fix"]}},
        },
        headers={"Authorization": "Bearer test-secret"},
    )
    assert response.status_code == 422


def test_ticket_status_not_found(client):
    """Test Ticket Status Not Found for this module."""
    response = client.get("/tickets/UNKNOWN-999")
    assert response.status_code == 404


def test_ticket_status_by_job_id(client, job_store):
    """Test Ticket Status By Job Id for this module."""
    job = job_store.enqueue("PLAT-99")
    response = client.get(f"/tickets/{job.job_id}")
    assert response.status_code == 200
    assert response.json()["status"] == TicketJobStatus.QUEUED.value


def test_extract_ticket_id_from_webhook():
    """Test Extract Ticket Id From Webhook for this module."""
    from src.api.ticket_jobs import extract_ticket_id_from_webhook

    assert extract_ticket_id_from_webhook({"issue": {"key": "abc-1"}}) == "ABC-1"
    assert extract_ticket_id_from_webhook({"ticket_id": "xyz-2"}) == "XYZ-2"
    assert extract_ticket_id_from_webhook({}) is None


def test_passes_webhook_filter_labels(monkeypatch):
    """Test Passes Webhook Filter Labels for this module."""
    from src.api.jira_filter import passes_webhook_filter
    from src.core import config

    monkeypatch.setattr(
        config,
        "settings",
        config.Settings(webhook_required_labels_raw="auto-fix"),
    )

    ok, _ = passes_webhook_filter(
        {
            "webhookEvent": "jira:issue_created",
            "issue": {"fields": {"labels": ["auto-fix"]}},
        }
    )
    assert ok is True

    blocked, reason = passes_webhook_filter(
        {
            "webhookEvent": "jira:issue_created",
            "issue": {"fields": {"labels": []}},
        }
    )
    assert blocked is False
    assert "auto-fix" in reason


def test_webhook_missing_api_key_config(monkeypatch, job_store):
    """Test Webhook Missing Api Key Config for this module."""
    from src.core import config
    from src.api import main as api_main
    from src.api import ticket_jobs as ticket_jobs_mod

    monkeypatch.setattr(config, "settings", config.Settings(webhook_api_key=""))
    monkeypatch.setattr(ticket_jobs_mod, "ticket_job_store", job_store)
    monkeypatch.setattr(api_main, "ticket_job_store", job_store)
    client = TestClient(api_main.app)
    response = client.post(
        "/jira/webhook",
        json=_labeled_issue("PLAT-1"),
        headers={"Authorization": "Bearer anything"},
    )
    assert response.status_code == 503
