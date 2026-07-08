"""FastAPI app — start pipeline by ticket id, poll status, resume HITL gates."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status

from src.api.schemas import (
    EnqueueTicketResponse,
    RunTicketRequest,
    TicketConflictResponse,
    TicketStatusResponse,
)
from src.api.ticket_jobs import TicketJobConflict, ticket_job_store
from src.core.schemas import ApprovalDecision


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Start the background job worker on startup."""
    await ticket_job_store.start_worker()
    yield
    await ticket_job_store.stop_worker()


app = FastAPI(title="Sprinklr Jira Solver API", lifespan=lifespan)


@app.get("/health")
def health():
    """Health for this module."""
    return {"status": "ok"}


@app.post("/run", response_model=EnqueueTicketResponse)
async def run_ticket(request: RunTicketRequest):
    """Enqueue a full pipeline run for a Jira ticket id."""
    ticket_id = request.ticket_id.strip().upper()
    if not ticket_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="ticket_id is required",
        )

    try:
        job = ticket_job_store.enqueue(
            ticket_id,
            user_prompt=request.user_prompt,
            repo_path=request.repo_path,
        )
    except TicketJobConflict as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=TicketConflictResponse(
                detail=f"Active job already exists for {exc.job.ticket_id}",
                job_id=exc.existing_job_id,
                ticket_id=exc.job.ticket_id,
                status=exc.job.status,
                thread_id=exc.job.thread_id,
            ).model_dump(),
        ) from exc

    return EnqueueTicketResponse(
        job_id=job.job_id,
        ticket_id=job.ticket_id,
        status=job.status,
        thread_id=job.thread_id,
        message="Ticket queued for processing",
    )


@app.get("/tickets/{ticket_or_job_id}", response_model=TicketStatusResponse)
async def get_ticket_status(ticket_or_job_id: str):
    """Return job status by ticket id (e.g. PLAT-1234) or job_id uuid."""
    job = ticket_job_store.resolve(ticket_or_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No job found for this ticket or job id",
        )
    return job.to_response()


@app.post("/approve/{thread_id}")
async def approve_workflow(thread_id: str, decision: ApprovalDecision):
    """Resume a paused workflow (HITL gate or diff confirm) via API."""
    job = ticket_job_store.get_job_by_thread(thread_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No paused job found for this thread_id",
        )

    resume = {"status": decision.status, "feedback": decision.feedback}
    try:
        from src.pipeline.graph_runner import resume_graph_until_pause

        outcome = await resume_graph_until_pause(thread_id, resume)
    except Exception as exc:
        return {"status": "error", "message": str(exc), "thread_id": thread_id}

    updated = await ticket_job_store.apply_resume_outcome(thread_id, outcome)
    if updated is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job disappeared while resuming",
        )

    response = {
        "status": updated.status.value,
        "thread_id": thread_id,
        "ticket_id": updated.ticket_id,
        "job_id": updated.job_id,
        "current_phase": updated.current_phase,
        "routing_route": updated.routing_route,
        "paused_gate": updated.paused_gate,
    }
    if updated.error:
        response["error"] = updated.error
    if updated.mr_url:
        response["mr_url"] = updated.mr_url
    return response
