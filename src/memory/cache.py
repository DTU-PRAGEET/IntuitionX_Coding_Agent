"""Redis-backed failed-attempt store."""

from __future__ import annotations

import json
import os
from typing import Any

from src.core.state import FailedAttempt


def _redis_client():
    import redis

    return redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))


def save_failed_attempt(attempt: FailedAttempt) -> None:
    try:
        r = _redis_client()
        key = f"failed:{attempt['ticket_id']}"
        existing: list[Any] = json.loads(r.get(key) or b"[]")
        existing.append(dict(attempt))
        r.set(key, json.dumps(existing), ex=60 * 60 * 24 * 30)
    except Exception:
        pass


def get_failed_attempts(ticket_id: str) -> list[FailedAttempt]:
    try:
        r = _redis_client()
        raw = r.get(f"failed:{ticket_id}")
        if not raw:
            return []
        return json.loads(raw)
    except Exception:
        return []
