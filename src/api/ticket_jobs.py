"""Redis-backed ticket job queue with bounded asyncio concurrency."""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

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

_log = get_logger("ticket_jobs")

SUPPORTED_WEBHOOK_EVENTS = frozenset(
    {
        "jira:issue_created",
        "jira:issue_updated",
    }
)

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


def _now_iso() -> str:
    """Now Iso.
    Supports the surrounding module workflow.
    """
    return datetime.now(timezone.utc).isoformat()


def extract_ticket_id_from_webhook(payload: dict[str, Any]) -> str | None:
    """Return Jira issue key from a webhook or test payload."""
    direct = payload.get("ticket_id")
    if isinstance(direct, str) and direct.strip():
        return direct.strip().upper()

    issue = payload.get("issue")
    if isinstance(issue, dict):
        key = issue.get("key")
        if isinstance(key, str) and key.strip():
            return key.strip().upper()
    return None


@dataclass
class TicketJob:
    """Ticket Job.
    Encapsulates related behavior for the Ticket Job component.
    """
    job_id: str
    ticket_id: str
    status: TicketJobStatus
    thread_id: str
    created_at: str
    updated_at: str
    webhook_event: str | None = None
    user_prompt: str = ""
    repo_path: str | None = None
    error: str | None = None
    routing_route: str | None = None
    current_phase: str | None = None
    mr_url: str | None = None
    exit_message: str | None = None
    paused_gate: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """To Dict on TicketJob."""
        return {
            "job_id": self.job_id,
            "ticket_id": self.ticket_id,
            "status": self.status.value,
            "thread_id": self.thread_id,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "webhook_event": self.webhook_event,
            "user_prompt": self.user_prompt,
            "repo_path": self.repo_path,
            "error": self.error,
            "routing_route": self.routing_route,
            "current_phase": self.current_phase,
            "mr_url": self.mr_url,
            "exit_message": self.exit_message,
            "paused_gate": self.paused_gate,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "TicketJob":
        """From Dict on TicketJob."""
        return cls(
            job_id=data["job_id"],
            ticket_id=data["ticket_id"],
            status=TicketJobStatus(data["status"]),
            thread_id=data["thread_id"],
            created_at=data["created_at"],
            updated_at=data["updated_at"],
            webhook_event=data.get("webhook_event"),
            user_prompt=data.get("user_prompt") or "",
            repo_path=data.get("repo_path"),
            error=data.get("error"),
            routing_route=data.get("routing_route"),
            current_phase=data.get("current_phase"),
            mr_url=data.get("mr_url"),
            exit_message=data.get("exit_message"),
            paused_gate=data.get("paused_gate"),
        )

    def to_response(self) -> TicketStatusResponse:
        """To Response on TicketJob."""
        return TicketStatusResponse(
            job_id=self.job_id,
            ticket_id=self.ticket_id,
            status=self.status,
            thread_id=self.thread_id,
            webhook_event=self.webhook_event,
            created_at=self.created_at,
            updated_at=self.updated_at,
            error=self.error,
            routing_route=self.routing_route,
            current_phase=self.current_phase,
            mr_url=self.mr_url,
            exit_message=self.exit_message,
            paused_gate=self.paused_gate,
        )


class TicketJobStore:
    """Redis job store + asyncio worker pool (Semaphore-bound)."""

    def __init__(self, redis_client: RedisCommands | None = None) -> None:
        """Initialize the TicketJobStore with the given configuration."""
        self._redis = redis_client or create_redis_client()
        self._semaphore = asyncio.Semaphore(settings.webhook_max_concurrency)
        self._worker_task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    def _save_job(self, job: TicketJob) -> None:
        """Save Job on TicketJobStore."""
        self._redis.set(job_key(job.job_id), dump_job(job.to_dict()))
        self._redis.set(ticket_latest_key(job.ticket_id), job.job_id)

    def get_job(self, job_id: str) -> TicketJob | None:
        """Return get job for the pipeline."""
        data = load_job(self._redis.get(job_key(job_id)))
        return TicketJob.from_dict(data) if data else None

    def get_job_by_thread(self, thread_id: str) -> TicketJob | None:
        """Return get job by thread for the pipeline."""
        job_id = self._redis.get(thread_key(thread_id))
        if not job_id:
            return None
        return self.get_job(job_id)

    def get_latest_for_ticket(self, ticket_id: str) -> TicketJob | None:
        """Return get latest for ticket for the pipeline."""
        job_id = self._redis.get(ticket_latest_key(ticket_id.upper()))
        if not job_id:
            return None
        return self.get_job(job_id)

    def get_active_for_ticket(self, ticket_id: str) -> TicketJob | None:
        """Return get active for ticket for the pipeline."""
        job_id = self._redis.get(ticket_active_key(ticket_id))
        if not job_id:
            return None
        job = self.get_job(job_id)
        if job and job.status.value in ACTIVE_STATUSES:
            return job
        if job_id:
            self._redis.delete(ticket_active_key(ticket_id))
        return None

    def resolve(self, ticket_id: str) -> TicketJob | None:
        """Resolve resolve for the pipeline."""
        job = self.get_job(ticket_id)
        if job:
            return job
        return self.get_latest_for_ticket(ticket_id)

    def enqueue(
        self,
        ticket_id: str,
        *,
        webhook_event: str | None = None,
        user_prompt: str = "",
        repo_path: str | None = None,
    ) -> TicketJob:
        """Enqueue a new job for the ticket or raise if one is already active."""
        ticket_id = ticket_id.upper()
        existing_id = self._redis.get(ticket_active_key(ticket_id))
        if existing_id:
            existing = self.get_job(existing_id)
            if existing and existing.status.value in ACTIVE_STATUSES:
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
            webhook_event=webhook_event,
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
            existing = self.get_job(existing_id) if existing_id else None
            if existing:
                raise TicketJobConflict(existing_id, existing)
            raise TicketJobConflict(existing_id, job)

        self._save_job(job)
        self._redis.set(thread_key(thread_id), job_id, ex=ACTIVE_TTL_SECONDS)
        self._redis.rpush(QUEUE_KEY, job_id)
        _log.info("ticket_enqueued", job_id=job_id, ticket_id=ticket_id)
        return job

    def _release_active(self, job: TicketJob) -> None:
        """Release Active on TicketJobStore."""
        active = self._redis.get(ticket_active_key(job.ticket_id))
        if active == job.job_id:
            self._redis.delete(ticket_active_key(job.ticket_id))

    def _apply_outcome(self, job: TicketJob, outcome: GraphRunOutcome) -> TicketJob:
        """Apply Outcome on TicketJobStore."""
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
            self._save_job(job)
            return job

        if outcome.kind == "failed":
            job.status = TicketJobStatus.FAILED
            job.error = outcome.error or state.get("current_error_log")
            self._release_active(job)
            self._save_job(job)
            return job

        job.status = TicketJobStatus.COMPLETED
        job.paused_gate = None
        if state.get("current_error_log"):
            job.status = TicketJobStatus.FAILED
            job.error = str(state["current_error_log"])[:4000]
            self._release_active(job)
        else:
            self._release_active(job)
        self._save_job(job)
        return job

    async def apply_resume_outcome(self, thread_id: str, outcome: GraphRunOutcome) -> TicketJob | None:
        """Apply apply resume outcome for the pipeline."""
        job = self.get_job_by_thread(thread_id)
        if job is None:
            return None
        if outcome.kind == "paused":
            job.status = TicketJobStatus.PAUSED
            job.paused_gate = outcome.gate
        elif outcome.kind == "failed":
            job.status = TicketJobStatus.FAILED
            job.error = outcome.error
            self._release_active(job)
        else:
            job.status = TicketJobStatus.COMPLETED
            job.paused_gate = None
            state = outcome.state or {}
            if state.get("current_error_log"):
                job.status = TicketJobStatus.FAILED
                job.error = str(state["current_error_log"])[:4000]
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
        """Start start worker for the pipeline."""
        if self._worker_task is not None and not self._worker_task.done():
            return
        self._stop_event.clear()
        self._worker_task = asyncio.create_task(self._worker_loop(), name="ticket-job-worker")

    async def stop_worker(self) -> None:
        """Stop stop worker for the pipeline."""
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
        """Worker Loop on TicketJobStore."""
        _log.info(
            "ticket_worker_started",
            max_concurrency=settings.webhook_max_concurrency,
        )
        while not self._stop_event.is_set():
            job_id = await asyncio.to_thread(self._blocking_dequeue, 1)
            if not job_id:
                continue
            asyncio.create_task(self._run_with_semaphore(job_id))

    def _blocking_dequeue(self, timeout: int) -> str | None:
        """Blocking Dequeue on TicketJobStore."""
        result = self._redis.blpop([QUEUE_KEY], timeout=timeout)
        if not result:
            return None
        _, job_id = result
        return job_id

    async def _run_with_semaphore(self, job_id: str) -> None:
        """Run With Semaphore on TicketJobStore."""
        async with self._semaphore:
            await self._process_job(job_id)

    async def _process_job(self, job_id: str) -> None:
        """Process Job on TicketJobStore."""
        job = self.get_job(job_id)
        if job is None:
            return

        job.status = TicketJobStatus.RUNNING
        job.updated_at = _now_iso()
        job.paused_gate = None
        self._save_job(job)
        _log.info("ticket_job_running", job_id=job_id, ticket_id=job.ticket_id)

        from src.pipeline.graph_runner import run_graph_until_pause

        outcome = await run_graph_until_pause(
            jira_ticket_id=job.ticket_id,
            thread_id=job.thread_id,
            job_id=job.job_id,
            user_prompt=job.user_prompt,
            repo_path=job.repo_path,
        )
        self._apply_outcome(job, outcome)
        _log.info(
            "ticket_job_finished",
            job_id=job_id,
            ticket_id=job.ticket_id,
            status=job.status.value,
        )


ticket_job_store = TicketJobStore()
