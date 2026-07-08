"""API access control hooks for job-scoped endpoints."""

from __future__ import annotations

from fastapi import HTTPException, status

from src.api import ticket_jobs


def verify_job_access(job_id: str) -> ticket_jobs.TicketJob:
    """Verify the caller may read events for this job.

    Phase 1: existence check only (404 if unknown).
    Phase 2: enforce API key or user-scoped ownership.
    """
    job = ticket_jobs.ticket_job_store.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No job found for this job id",
        )
    return job
