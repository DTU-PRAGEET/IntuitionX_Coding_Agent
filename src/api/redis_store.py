"""Redis persistence for ticket job queue and status."""

from __future__ import annotations

import json
from typing import Any, Protocol

import redis

from src.core.config import settings

KEY_PREFIX = "jira_solver"
QUEUE_KEY = f"{KEY_PREFIX}:queue"
ACTIVE_TTL_SECONDS = 60 * 60 * 24 * 7  # 7 days safety TTL on active ticket keys


def job_key(job_id: str) -> str:
    """Job Key.
    Supports the surrounding module workflow.
    """
    return f"{KEY_PREFIX}:job:{job_id}"


def ticket_active_key(ticket_id: str) -> str:
    """Ticket Active Key.
    Supports the surrounding module workflow.
    """
    return f"{KEY_PREFIX}:ticket_active:{ticket_id.upper()}"


def thread_key(thread_id: str) -> str:
    """Thread Key.
    Supports the surrounding module workflow.
    """
    return f"{KEY_PREFIX}:thread:{thread_id}"


def ticket_latest_key(ticket_id: str) -> str:
    """Ticket Latest Key.
    Supports the surrounding module workflow.
    """
    return f"{KEY_PREFIX}:ticket_latest:{ticket_id.upper()}"


def events_key(job_id: str) -> str:
    """Redis list key for ordered pipeline events."""
    return f"{KEY_PREFIX}:events:{job_id}"


def events_seq_key(job_id: str) -> str:
    """Redis counter key for monotonic event sequence numbers."""
    return f"{KEY_PREFIX}:events:{job_id}:seq"


class RedisCommands(Protocol):
    """Protocol subset of Redis commands used by the job store."""

    def get(self, name: str) -> bytes | str | None:
        """Fetch a string value by key."""
        ...

    def set(self, name: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool | None:
        """Store a string value with optional TTL and NX semantics."""
        ...

    def delete(self, *names: str) -> int:
        """Delete one or more keys and return how many were removed."""
        ...

    def rpush(self, name: str, *values: str) -> int:
        """Append values to the tail of a list."""
        ...

    def blpop(self, keys: list[str], timeout: int = 0) -> tuple[str, str] | None:
        """Blocking pop from the first non-empty list among keys."""
        ...

    def lpush(self, name: str, *values: str) -> int:
        """Prepend values to the head of a list."""
        ...

    def lrange(self, name: str, start: int, end: int) -> list[str]:
        """Return a slice of list elements by index range."""
        ...

    def lrem(self, name: str, count: int, value: str) -> int:
        """Remove list elements equal to value (count=0 removes all matches)."""
        ...

    def incr(self, name: str) -> int:
        """Increment a counter and return the new value."""
        ...

    def expire(self, name: str, time: int) -> bool:
        """Set a TTL in seconds on a key."""
        ...


class DictRedisClient:
    """Minimal in-memory Redis subset for unit tests."""

    def __init__(self) -> None:
        """Initialize the DictRedisClient with the given configuration."""
        self._kv: dict[str, str] = {}
        self._lists: dict[str, list[str]] = {}
        self._counters: dict[str, int] = {}
        self._ttl: dict[str, int] = {}

    def get(self, name: str) -> str | None:
        """Return get for the pipeline."""
        return self._kv.get(name)

    def set(self, name: str, value: str, *, ex: int | None = None, nx: bool = False) -> bool | None:
        """Set set for the pipeline."""
        if nx and name in self._kv:
            return None
        self._kv[name] = value
        return True

    def delete(self, *names: str) -> int:
        """Remove delete for the pipeline."""
        count = 0
        for name in names:
            if name in self._kv:
                del self._kv[name]
                count += 1
            if name in self._lists:
                del self._lists[name]
                count += 1
        return count

    def rpush(self, name: str, *values: str) -> int:
        """Rpush on DictRedisClient."""
        self._lists.setdefault(name, []).extend(values)
        return len(self._lists[name])

    def blpop(self, keys: list[str], timeout: int = 0) -> tuple[str, str] | None:
        """Blpop on DictRedisClient."""
        for key in keys:
            items = self._lists.get(key) or []
            if items:
                value = items.pop(0)
                return key, value
        return None

    def lpush(self, name: str, *values: str) -> int:
        """Lpush on DictRedisClient."""
        self._lists.setdefault(name, [])
        for value in reversed(values):
            self._lists[name].insert(0, value)
        return len(self._lists[name])

    def lrange(self, name: str, start: int, end: int) -> list[str]:
        """Lrange on DictRedisClient."""
        items = self._lists.get(name) or []
        if end == -1:
            end = len(items) - 1
        return items[start : end + 1]

    def lrem(self, name: str, count: int, value: str) -> int:
        """Lrem on DictRedisClient."""
        items = self._lists.get(name) or []
        removed = 0
        if count == 0:
            while value in items:
                items.remove(value)
                removed += 1
        elif count > 0:
            for _ in range(count):
                if value in items:
                    items.remove(value)
                    removed += 1
        else:
            for idx in range(len(items) - 1, -1, -1):
                if items[idx] == value:
                    items.pop(idx)
                    removed += 1
                    if removed >= abs(count):
                        break
        return removed

    def incr(self, name: str) -> int:
        """Incr on DictRedisClient."""
        self._counters[name] = self._counters.get(name, 0) + 1
        self._kv[name] = str(self._counters[name])
        return self._counters[name]

    def expire(self, name: str, time: int) -> bool:
        """Expire on DictRedisClient (records TTL for test introspection)."""
        self._ttl[name] = time
        return True


def create_redis_client(url: str | None = None) -> RedisCommands:
    """Create create redis client for the pipeline."""
    return redis.Redis.from_url(url or settings.redis_url, decode_responses=True)


def dump_job(job: dict[str, Any]) -> str:
    """Dump Job.
    Supports the surrounding module workflow.
    """
    return json.dumps(job)


def load_job(raw: str | bytes | None) -> dict[str, Any] | None:
    """Load load job for the pipeline."""
    if not raw:
        return None
    if isinstance(raw, bytes):
        raw = raw.decode()
    return json.loads(raw)
