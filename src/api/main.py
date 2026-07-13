"""FastAPI app — start pipeline by ticket id, poll status, resume HITL gates."""

from __future__ import annotations

import asyncio
import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import PlainTextResponse, StreamingResponse

from src.api.execution_events import TERMINAL_EVENT_TYPES, event_store
from src.api.pause_display import pause_display_message, pause_display_with_actions
from src.api.schemas import (
    CancelJobResponse,
    EnqueueTicketResponse,
    EventsListResponse,
    RunTicketRequest,
    TicketConflictResponse,
    TicketJobStatus,
    TicketStatusResponse,
)
from src.api.ticket_jobs import (
    TicketJob,
    TicketJobConflict,
    TicketJobNotCancellable,
    TicketJobNotFound,
    ticket_job_store,
)
from src.core.schemas import ApprovalDecision


def verify_job_access(job_id: str) -> TicketJob:
    """Verify the caller may read events for this job.

    Phase 1: existence check only (404 if unknown).
    Phase 2: enforce API key or user-scoped ownership.
    """
    job = ticket_job_store.get_job(job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No job found for this job id",
        )
    return job


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
    return job.to_response(event_store)


@app.delete("/tickets/{ticket_or_job_id}", response_model=CancelJobResponse)
async def cancel_ticket_job(ticket_or_job_id: str):
    """Cancel an active job (queued, paused, or stale running) and release the ticket lock."""
    try:
        job = ticket_job_store.cancel_active_job(ticket_or_job_id)
    except TicketJobNotFound as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except TicketJobNotCancellable as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={
                "detail": exc.detail,
                "job_id": exc.job.job_id,
                "ticket_id": exc.job.ticket_id,
                "status": exc.job.status.value,
                "thread_id": exc.job.thread_id,
            },
        ) from exc

    return CancelJobResponse(
        job_id=job.job_id,
        ticket_id=job.ticket_id,
        status=job.status,
        message=f"Job cancelled for {job.ticket_id}; you can POST /run to start fresh",
    )


@app.get("/tickets/{ticket_or_job_id}/pause", response_class=PlainTextResponse)
async def get_pause_display(ticket_or_job_id: str):
    """Human-readable HITL gate message (plain text, same as Chainlit shows)."""
    job = ticket_job_store.resolve(ticket_or_job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No job found for this ticket or job id",
        )
    if job.status != TicketJobStatus.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Job is not paused (current status: {job.status.value})",
        )
    return pause_display_with_actions(
        status=job.status,
        pause_context=job.pause_context,
        thread_id=job.thread_id,
        job_id=job.job_id,
    )


@app.get("/jobs/{job_id}/events", response_model=EventsListResponse)
async def list_job_events(
    job_id: str,
    after_seq: int = 0,
    limit: int = 100,
    _job: TicketJob = Depends(verify_job_access),
):
    """Poll ordered pipeline events for a job."""
    if limit < 1 or limit > 500:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="limit must be between 1 and 500",
        )
    if after_seq < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="after_seq must be >= 0",
        )
    return event_store.list_events(job_id, after_seq=after_seq, limit=limit)


@app.get("/jobs/{job_id}/events/stream")
async def stream_job_events(
    job_id: str,
    after_seq: int = 0,
    _job: TicketJob = Depends(verify_job_access),
):
    """SSE stream of pipeline events; reconnect with after_seq for replay."""
    if after_seq < 0:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="after_seq must be >= 0",
        )

    async def event_generator():
        last_seq = after_seq
        poll_interval = 0.3
        while True:
            result = event_store.list_events(job_id, after_seq=last_seq, limit=100)
            for event in result.events:
                if event.type == "waiting_for_human_approval":
                    message = event.payload.get("message") or ""
                    if message:
                        yield "event: approval_required\n"
                        for line in message.splitlines():
                            yield f"data: {line}\n"
                        yield "\n"
                yield f"data: {json.dumps(event.model_dump())}\n\n"
                last_seq = event.seq
                if event.type in TERMINAL_EVENT_TYPES:
                    return

            job = ticket_job_store.get_job(job_id)
            if job and job.status in (
                TicketJobStatus.COMPLETED,
                TicketJobStatus.FAILED,
                TicketJobStatus.CANCELLED,
            ):
                if not result.events:
                    return
                if result.events[-1].type in TERMINAL_EVENT_TYPES:
                    return

            if event_store.is_terminal(job_id) and not result.events:
                return

            await asyncio.sleep(poll_interval)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


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
        from src.api.execution_events import RedisEventSink
        from src.pipeline.graph_runner import resume_graph_until_pause

        sink = RedisEventSink(job.job_id, job.thread_id, event_store)
        outcome = await resume_graph_until_pause(thread_id, resume, event_sink=sink)
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
    if updated.pause_context:
        response["pause_context"] = updated.pause_context
    display = pause_display_message(
        status=updated.status,
        pause_context=updated.pause_context,
    )
    if display:
        response["display_message"] = display
    return response
