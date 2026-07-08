"""Redis-backed ticket job queue with bounded asyncio concurrency."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.api.execution_events import (
    ExecutionEventStore,
    RedisEventSink,
    event_store,
)
from src.api.pause_display import pause_display_message
from src.api.redis_store import (
    ACTIVE_TTL_SECONDS,
    QUEUE_KEY,
    DictRedisClient,
    RedisCommands,
    create_redis_client,
    dump_job,
    job_key,
    load_job,
    thread_key,
    ticket_active_key,
    ticket_latest_key,
)
from src.api.schemas import TicketJobStatus, TicketStatusResponse
from src.core.config import settings
from src.core.logging import get_logger
from src.pipeline.graph_outcome import GraphRunOutcome
from src.pipeline.pipeline_event_factory import PipelineEventFactory

_log = get_logger("ticket_jobs")

ACTIVE_STATUSES = frozenset(
    {
        TicketJobStatus.QUEUED.value,
        TicketJobStatus.RUNNING.value,
        TicketJobStatus.PAUSED.value,
    }
)


class TicketJobConflict(Exception):
    """Raised when a ticket already has an active job."""

    def __init__(self, existing_job_id: str, job: "TicketJob") -> None:
        """Initialize the TicketJobConflict with the given configuration."""
        self.existing_job_id = existing_job_id
        self.job = job
        super().__init__(f"Active job already exists for {job.ticket_id}")


class TicketJobNotFound(Exception):
    """Raised when no job exists for the given id."""


class TicketJobNotCancellable(Exception):
    """Raised when a job cannot be cancelled (not active or still executing)."""

    def __init__(self, job: "TicketJob", detail: str) -> None:
        self.job = job
        self.detail = detail
        super().__init__(detail)


def _now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


@dataclass
class TicketJob:
    """One pipeline run started via POST /run."""

    job_id: str
    ticket_id: str
    status: TicketJobStatus
    thread_id: str
    created_at: str
    updated_at: str
    user_prompt: str = ""
    repo_path: str | None = None
    error: str | None = None
    routing_route: str | None = None
    current_phase: str | None = None
    mr_url: str | None = None
    exit_message: str | None = None
    paused_gate: str | None = None
    pause_context: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialize job for Redis storage."""
        return {
            "job_id": self.job_id,
            "ticket_id": self.ticket_id,
            "status": self.status.value,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "user_prompt": self.user_prompt,
            "repo_path": self.repo_path,
            "error": self.error,
            "routing_route": self.routing_route,
            "current_phase": self.current_phase,
            "mr_url": self.mr_url,
            "exit_message": self.exit_message,
            "paused_gate": self.paused_gate,
            "pause_context": self.pause_context,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TicketJob":
        """Deserialize job from Redis storage."""
        return cls(
            job_id=data["job_id"],
            ticket_id=data["ticket_id"],
            status=TicketJobStatus(data["status"]),
            thread_id=data["thread_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            user_prompt=data.get("user_prompt") or "",
            repo_path=data.get("repo_path"),
            error=data.get("error"),
            routing_route=data.get("routing_route"),
            current_phase=data.get("current_phase"),
            mr_url=data.get("mr_url"),
            exit_message=data.get("exit_message"),
            paused_gate=data.get("paused_gate"),
            pause_context=data.get("pause_context"),
        )

    def to_response(self, store: ExecutionEventStore | None = None) -> TicketStatusResponse:
        """Build API status response."""
        latest_seq = None
        if store is not None:
            latest_seq = store.latest_seq(self.job_id) or None
        return TicketStatusResponse(
            job_id=self.job_id,
            ticket_id=self.ticket_id,
            status=self.status,
            thread_id=self.thread_id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            error=self.error,
            routing_route=self.routing_route,
            current_phase=self.current_phase,
            mr_url=self.mr_url,
            exit_message=self.exit_message,
            paused_gate=self.paused_gate,
            pause_context=self.pause_context if self.status == TicketJobStatus.PAUSED else None,
            latest_event_seq=latest_seq,
            display_message=pause_display_message(
                status=self.status,
                pause_context=self.pause_context,
            ),
        )


class TicketJobStore:
    """Redis job store + asyncio worker pool (Semaphore-bound)."""

    def __init__(
        self,
        redis_client: RedisCommands | None = None,
        *,
        events: ExecutionEventStore | None = None,
    ) -> None:
        """Initialize the TicketJobStore with the given configuration."""
        self._redis = redis_client or create_redis_client()
        self._events = events or event_store
        self._semaphore = asyncio.Semaphore(settings.api_max_concurrency)
        self._worker_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._in_flight: set[str] = set()

    def _make_sink(self, job: TicketJob) -> RedisEventSink:
        """Create an immutable event sink for one job execution segment."""
        return RedisEventSink(job.job_id, job.thread_id, self._events)

    def _emit_lifecycle(self, job: TicketJob, event_type: str, payload: dict[str, Any] | None = None) -> None:
        """Emit a job lifecycle event through the job's sink."""
        event_type, event_payload = PipelineEventFactory.lifecycle(event_type, payload)
        self._make_sink(job).emit(event_type, event_payload)

    def _save_job(self, job: TicketJob) -> None:
        """Persist job to Redis."""
        self._redis.set(job_key(job.job_id), dump_job(job.to_dict()))
        self._redis.set(ticket_latest_key(job.ticket_id), job.job_id)

    def get_job(self, job_id: str) -> TicketJob | None:
        """Load job by job_id."""
        data = load_job(self._redis.get(job_key(job_id)))
        return TicketJob.from_dict(data) if data else None

    def get_job_by_thread(self, thread_id: str) -> TicketJob | None:
        """Load job by LangGraph thread_id."""
        job_id = self._redis.get(thread_key(thread_id))
        if not job_id:
            return None
        return self.get_job(job_id)

    def get_latest_for_ticket(self, ticket_id: str) -> TicketJob | None:
        """Load most recent job for a ticket id."""
        job_id = self._redis.get(ticket_latest_key(ticket_id.upper()))
        if not job_id:
            return None
        return self.get_job(job_id)

    def get_active_for_ticket(self, ticket_id: str) -> TicketJob | None:
        """Return active job for ticket if one exists."""
        job_id = self._redis.get(ticket_active_key(ticket_id))
        if not job_id:
            return None
        job = self.get_job(job_id)
        if job and job.status.value in ACTIVE_STATUSES:
            return job
        if job_id:
            self._redis.delete(ticket_active_key(ticket_id))
        return None

    def resolve(self, ticket_or_job_id: str) -> TicketJob | None:
        """Resolve by job_id uuid or ticket id (e.g. PLAT-1234)."""
        job = self.get_job(ticket_or_job_id)
        if job:
            return job
        return self.get_latest_for_ticket(ticket_or_job_id)

    def _remove_from_queue(self, job_id: str) -> None:
        """Remove a job id from the Redis work queue (no-op if absent)."""
        self._redis.lrem(QUEUE_KEY, 0, job_id)

    def cancel_active_job(
        self,
        ticket_or_job_id: str,
        *,
        reason: str = "Cancelled by user",
    ) -> TicketJob:
        """Cancel queued/paused job and release the ticket lock so POST /run can start fresh."""
        job = self.resolve(ticket_or_job_id)
        if job is None:
            raise TicketJobNotFound(f"No job found for {ticket_or_job_id!r}")

        if job.status.value not in ACTIVE_STATUSES:
            raise TicketJobNotCancellable(
                job,
                f"Job is not active (current status: {job.status.value})",
            )

        if job.status == TicketJobStatus.RUNNING and job.job_id in self._in_flight:
            raise TicketJobNotCancellable(
                job,
                "Job is currently executing; wait until it pauses or fails, then cancel",
            )

        previous_status = job.status.value
        self._remove_from_queue(job.job_id)
        job.status = TicketJobStatus.CANCELLED
        job.error = reason
        job.exit_message = reason
        job.paused_gate = None
        job.pause_context = None
        job.updated_at = _now_iso()
        self._release_active(job)
        self._save_job(job)
        self._emit_lifecycle(
            job,
            "job_cancelled",
            {"ticket_id": job.ticket_id, "reason": reason},
        )
        _log.info(
            "ticket_job_cancelled",
            job_id=job.job_id,
            ticket_id=job.ticket_id,
            previous_status=previous_status,
        )
        return job

    def enqueue(
        self,
        ticket_id: str,
        *,
        user_prompt: str = "",
        repo_path: str | None = None,
    ) -> TicketJob:
        """Enqueue a new job for the ticket or raise if one is already active."""
        ticket_id = ticket_id.upper()
        existing_id = self._redis.get(ticket_active_key(ticket_id))
        if existing_id:
            existing = self._clear_active_if_not_running(ticket_id, existing_id)
            if existing is not None:
                raise TicketJobConflict(existing_id, existing)

        now = _now_iso()
        job_id = str(uuid.uuid4())
        thread_id = str(uuid.uuid4())
        job = TicketJob(
            job_id=job_id,
            ticket_id=ticket_id,
            status=TicketJobStatus.QUEUED,
            thread_id=thread_id,
            created_at=now,
            updated_at=now,
            user_prompt=user_prompt,
            repo_path=repo_path,
        )

        claimed = self._redis.set(
            ticket_active_key(ticket_id),
            job_id,
            nx=True,
            ex=ACTIVE_TTL_SECONDS,
        )
        if not claimed:
            existing_id = self._redis.get(ticket_active_key(ticket_id)) or ""
            existing = (
                self._clear_active_if_not_running(ticket_id, existing_id)
                if existing_id
                else None
            )
            if existing is not None:
                raise TicketJobConflict(existing_id, existing)
            raise TicketJobConflict(existing_id, job)

        self._save_job(job)
        self._redis.set(thread_key(thread_id), job_id, ex=ACTIVE_TTL_SECONDS)
        self._redis.rpush(QUEUE_KEY, job_id)
        self._emit_lifecycle(job, "job_queued", {"ticket_id": ticket_id})
        _log.info("ticket_enqueued", job_id=job_id, ticket_id=ticket_id)
        return job

    def _release_active(self, job: TicketJob) -> None:
        """Clear active-ticket lock when job finishes."""
        active = self._redis.get(ticket_active_key(job.ticket_id))
        if active == job.job_id:
            self._redis.delete(ticket_active_key(job.ticket_id))

    def _reconcile_stale_job(self, job: TicketJob) -> bool:
        """Mark zombie RUNNING jobs failed after worker crash/restart."""
        if job.status != TicketJobStatus.RUNNING:
            return False
        if job.job_id in self._in_flight:
            return False
        job.status = TicketJobStatus.FAILED
        job.error = job.error or "Previous run lost (worker restarted or crashed)"
        job.updated_at = _now_iso()
        self._release_active(job)
        self._save_job(job)
        _log.warning(
            "ticket_job_reconciled",
            job_id=job.job_id,
            ticket_id=job.ticket_id,
        )
        return True

    def _clear_active_if_not_running(self, ticket_id: str, existing_id: str) -> TicketJob | None:
        """Return blocking active job, reconciling stale/orphan locks first."""
        existing = self.get_job(existing_id)
        if existing is None:
            self._redis.delete(ticket_active_key(ticket_id))
            return None
        if self._reconcile_stale_job(existing):
            return None
        if existing.status.value not in ACTIVE_STATUSES:
            self._release_active(existing)
            return None
        return existing

    def _apply_outcome(self, job: TicketJob, outcome: GraphRunOutcome) -> TicketJob:
        """Update job fields from a graph run outcome."""
        job.updated_at = _now_iso()
        state = outcome.state or {}
        job.routing_route = state.get("routing_route")
        job.current_phase = state.get("current_phase")
        job.exit_message = state.get("exit_message")
        execution = state.get("execution_output") or {}
        job.mr_url = execution.get("mr_url") if isinstance(execution, dict) else None

        if outcome.kind == "paused":
            job.status = TicketJobStatus.PAUSED
            job.paused_gate = outcome.gate
            job.pause_context = outcome.interrupt_payload
            self._save_job(job)
            return job

        job.pause_context = None
        if outcome.kind == "failed":
            job.status = TicketJobStatus.FAILED
            job.error = outcome.error or state.get("current_error_log")
            self._release_active(job)
            self._save_job(job)
            self._emit_lifecycle(
                job,
                "execution_failed",
                {"error": job.error, "ticket_id": job.ticket_id},
            )
            return job

        job.status = TicketJobStatus.COMPLETED
        job.paused_gate = None
        if state.get("current_error_log"):
            job.status = TicketJobStatus.FAILED
            job.error = str(state["current_error_log"])[:4000]
            self._release_active(job)
            self._emit_lifecycle(
                job,
                "execution_failed",
                {"error": job.error, "ticket_id": job.ticket_id},
            )
        else:
            self._release_active(job)
            self._emit_lifecycle(
                job,
                "execution_completed",
                {
                    "ticket_id": job.ticket_id,
                    "mr_url": job.mr_url,
                    "routing_route": job.routing_route,
                },
            )
        self._save_job(job)
        return job

    async def apply_resume_outcome(self, thread_id: str, outcome: GraphRunOutcome) -> TicketJob | None:
        """Update job after POST /approve resumes the graph."""
        job = self.get_job_by_thread(thread_id)
        if job is None:
            return None
        if outcome.kind == "paused":
            job.status = TicketJobStatus.PAUSED
            job.paused_gate = outcome.gate
            job.pause_context = outcome.interrupt_payload
        elif outcome.kind == "failed":
            job.status = TicketJobStatus.FAILED
            job.error = outcome.error
            job.pause_context = None
            self._release_active(job)
            self._emit_lifecycle(
                job,
                "execution_failed",
                {"error": job.error, "ticket_id": job.ticket_id},
            )
        else:
            job.status = TicketJobStatus.COMPLETED
            job.paused_gate = None
            job.pause_context = None
            state = outcome.state or {}
            if state.get("current_error_log"):
                job.status = TicketJobStatus.FAILED
                job.error = str(state["current_error_log"])[:4000]
                self._emit_lifecycle(
                    job,
                    "execution_failed",
                    {"error": job.error, "ticket_id": job.ticket_id},
                )
            else:
                self._emit_lifecycle(
                    job,
                    "execution_completed",
                    {
                        "ticket_id": job.ticket_id,
                        "mr_url": job.mr_url,
                        "routing_route": job.routing_route,
                    },
                )
            self._release_active(job)
        job.updated_at = _now_iso()
        state = outcome.state or {}
        job.routing_route = state.get("routing_route") or job.routing_route
        job.current_phase = state.get("current_phase") or job.current_phase
        job.exit_message = state.get("exit_message") or job.exit_message
        execution = state.get("execution_output") or {}
        if isinstance(execution, dict) and execution.get("mr_url"):
            job.mr_url = execution.get("mr_url")
        self._save_job(job)
        return job

    async def start_worker(self) -> None:
        """Start background worker that drains the job queue."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop(), name="ticket-job-worker")

    async def stop_worker(self) -> None:
        """Stop background worker."""
        self._stop_event.set()
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except asyncio.CancelledError:
            pass
        self._worker_task = None

    async def _worker_loop(self) -> None:
        """Poll Redis queue and spawn bounded concurrent job runs."""
        _log.info(
            "ticket_worker_started",
            max_concurrency=settings.api_max_concurrency,
        )
        while not self._stop_event.is_set():
            job_id = await asyncio.to_thread(self._blocking_dequeue, 1)
            if not job_id:
                continue
            asyncio.create_task(self._run_with_semaphore(job_id))

    def _blocking_dequeue(self, timeout: int) -> str | None:
        """Blocking Redis BLPOP for the next job id."""
        result = self._redis.blpop([QUEUE_KEY], timeout=timeout)
        if not result:
            return None
        _, job_id = result
        return job_id

    async def _run_with_semaphore(self, job_id: str) -> None:
        """Run one job under the concurrency semaphore."""
        self._in_flight.add(job_id)
        try:
            async with self._semaphore:
                await self._process_job(job_id)
        except Exception as exc:
            _log.exception("ticket_job_worker_failed", job_id=job_id)
            job = self.get_job(job_id)
            if job and job.status == TicketJobStatus.RUNNING:
                self._apply_outcome(
                    job,
                    GraphRunOutcome(kind="failed", state={}, error=str(exc)[:4000]),
                )
        finally:
            self._in_flight.discard(job_id)

    async def _process_job(self, job_id: str) -> None:
        """Execute one queued pipeline job."""
        job = self.get_job(job_id)
        if job is None:
            return
        if job.status == TicketJobStatus.CANCELLED:
            _log.info("ticket_job_skip_cancelled", job_id=job_id)
            return

        job.status = TicketJobStatus.RUNNING
        job.updated_at = _now_iso()
        job.paused_gate = None
        job.pause_context = None
        self._save_job(job)
        self._emit_lifecycle(job, "job_running", {"ticket_id": job.ticket_id})
        _log.info("ticket_job_running", job_id=job_id, ticket_id=job.ticket_id)

        from src.pipeline.graph_runner import run_graph_until_pause

        sink = self._make_sink(job)
        try:
            outcome = await run_graph_until_pause(
                jira_ticket_id=job.ticket_id,
                thread_id=job.thread_id,
                user_prompt=job.user_prompt,
                repo_path=job.repo_path,
                event_sink=sink,
            )
        except Exception as exc:
            _log.exception("ticket_job_failed", job_id=job_id, ticket_id=job.ticket_id)
            outcome = GraphRunOutcome(kind="failed", state={}, error=str(exc)[:4000])
        self._apply_outcome(job, outcome)
        _log.info(
            "ticket_job_finished",
            job_id=job_id,
            ticket_id=job.ticket_id,
            status=job.status.value,
        )


ticket_job_store = TicketJobStore()
