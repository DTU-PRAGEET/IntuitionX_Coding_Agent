"""Redis-backed pipeline execution event store and sink."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Protocol

from src.api.redis_store import (
    ACTIVE_TTL_SECONDS,
    RedisCommands,
    create_redis_client,
    events_key,
    events_seq_key,
)
from src.api.schemas import EventsListResponse, PipelineEvent

TERMINAL_EVENT_TYPES = frozenset({"execution_completed", "execution_failed", "job_cancelled"})


def _now_iso() -> str:
    """Return current UTC timestamp in ISO format."""
    return datetime.now(timezone.utc).isoformat()


class ExecutionEventSink(Protocol):
    """Observer sink for pipeline execution events."""

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Persist one semantic pipeline event."""
        ...


class ExecutionEventStore:
    """Append-only per-job event log backed by Redis."""

    def __init__(self, redis_client: RedisCommands | None = None) -> None:
        """Initialize store with optional Redis client (for tests)."""
        self._redis = redis_client or create_redis_client()

    def append(
        self,
        job_id: str,
        thread_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> PipelineEvent:
        """Append one event and return the stored record."""
        seq = self._redis.incr(events_seq_key(job_id))
        event = PipelineEvent(
            seq=seq,
            timestamp=_now_iso(),
            type=event_type,
            job_id=job_id,
            thread_id=thread_id,
            payload=payload or {},
        )
        raw = json.dumps(event.model_dump())
        list_key = events_key(job_id)
        self._redis.rpush(list_key, raw)
        self._redis.expire(list_key, ACTIVE_TTL_SECONDS)
        self._redis.expire(events_seq_key(job_id), ACTIVE_TTL_SECONDS)
        return event

    def latest_seq(self, job_id: str) -> int:
        """Return latest sequence number (0 if no events)."""
        raw = self._redis.get(events_seq_key(job_id))
        if raw is None:
            return 0
        return int(raw)

    def list_events(
        self,
        job_id: str,
        *,
        after_seq: int = 0,
        limit: int = 100,
    ) -> EventsListResponse:
        """Return events with seq > after_seq, up to limit."""
        all_raw = self._redis.lrange(events_key(job_id), 0, -1)
        events: list[PipelineEvent] = []
        for raw in all_raw:
            data = json.loads(raw)
            if data["seq"] <= after_seq:
                continue
            events.append(PipelineEvent.model_validate(data))
            if len(events) >= limit:
                break

        latest = self.latest_seq(job_id)
        has_more = bool(events) and events[-1].seq < latest
        if not events and latest > after_seq:
            has_more = True

        return EventsListResponse(
            job_id=job_id,
            events=events,
            latest_seq=latest,
            has_more=has_more,
        )

    def is_terminal(self, job_id: str) -> bool:
        """True if the latest event is a terminal lifecycle event."""
        all_raw = self._redis.lrange(events_key(job_id), 0, -1)
        if not all_raw:
            return False
        last = json.loads(all_raw[-1])
        return last.get("type") in TERMINAL_EVENT_TYPES


class RedisEventSink:
    """Immutable per-execution sink that writes to a job-scoped Redis log."""

    __slots__ = ("_job_id", "_store", "_thread_id")

    def __init__(
        self,
        job_id: str,
        thread_id: str,
        store: ExecutionEventStore,
    ) -> None:
        """Bind sink to one job/thread pair for the duration of a graph segment."""
        self._job_id = job_id
        self._thread_id = thread_id
        self._store = store

    @property
    def job_id(self) -> str:
        """Job id this sink writes to."""
        return self._job_id

    @property
    def thread_id(self) -> str:
        """LangGraph thread id stamped on every event."""
        return self._thread_id

    def emit(self, event_type: str, payload: dict[str, Any] | None = None) -> PipelineEvent:
        """Append one event to this job's log."""
        return self._store.append(
            self._job_id,
            self._thread_id,
            event_type,
            payload,
        )


event_store = ExecutionEventStore()
