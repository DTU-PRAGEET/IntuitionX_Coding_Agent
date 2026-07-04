"""Pydantic models for the FastAPI webhook layer."""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class TicketJobStatus(str, Enum):
    """Ticket Job Status.
    Encapsulates related behavior for the Ticket Job Status component.
    """
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class JiraWebhookPayload(BaseModel):
    """Minimal Jira webhook body — extra fields are ignored."""

    webhookEvent: str | None = None
    issue: dict[str, Any] | None = None
    ticket_id: str | None = Field(
        default=None,
        description="Optional direct ticket key for testing without full Jira payload",
    )
    user_prompt: str = ""
    repo_path: str | None = None


class EnqueueTicketResponse(BaseModel):
    """Enqueue Ticket Response.
    Encapsulates related behavior for the Enqueue Ticket Response component.
    """
    job_id: str
    ticket_id: str
    status: TicketJobStatus
    message: str


class TicketStatusResponse(BaseModel):
    """Ticket Status Response.
    Encapsulates related behavior for the Ticket Status Response component.
    """
    job_id: str
    ticket_id: str
    status: TicketJobStatus
    thread_id: str | None = None
    webhook_event: str | None = None
    created_at: str
    updated_at: str
    error: str | None = None
    routing_route: str | None = None
    current_phase: str | None = None
    mr_url: str | None = None
    exit_message: str | None = None
    paused_gate: str | None = None


class WebhookIgnoredResponse(BaseModel):
    """Webhook Ignored Response.
    Encapsulates related behavior for the Webhook Ignored Response component.
    """
    status: str = "ignored"
    ticket_id: str | None = None
    message: str


class TicketConflictResponse(BaseModel):
    """Ticket Conflict Response.
    Encapsulates related behavior for the Ticket Conflict Response component.
    """
    detail: str
    job_id: str
    ticket_id: str
    status: TicketJobStatus
    thread_id: str | None = None
