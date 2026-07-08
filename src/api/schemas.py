"""Pydantic models for the FastAPI layer."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class TicketJobStatus(str, Enum):
    """Lifecycle of a pipeline job started via the API."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"


class RunTicketRequest(BaseModel):
    """Body for POST /run — start the full pipeline for one Jira ticket."""

    ticket_id: str = Field(..., description="Jira issue key, e.g. PLAT-1234")
    user_prompt: str = ""
    repo_path: str | None = Field(
        default=None,
        description="Optional repo path override; default resolves from Jira ticket",
    )


class EnqueueTicketResponse(BaseModel):
    """Response after enqueueing a pipeline run."""

    job_id: str
    ticket_id: str
    status: TicketJobStatus
    thread_id: str
    message: str


class TicketStatusResponse(BaseModel):
    """Current status of a pipeline job."""

    job_id: str
    ticket_id: str
    status: TicketJobStatus
    thread_id: str | None = None
    created_at: str
    updated_at: str
    error: str | None = None
    routing_route: str | None = None
    current_phase: str | None = None
    mr_url: str | None = None
    exit_message: str | None = None
    paused_gate: str | None = None


class TicketConflictResponse(BaseModel):
    """Returned when the same ticket already has an active job."""

    detail: str
    job_id: str
    ticket_id: str
    status: TicketJobStatus
    thread_id: str | None = None
