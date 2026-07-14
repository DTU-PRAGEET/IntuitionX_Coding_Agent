"""MongoDB-backed failed-attempt store (durable; no TTL)."""

from __future__ import annotations

import os
from typing import Any

from src.core.state import FailedAttempt

_DB_NAME = os.getenv("MONGO_FAILED_ATTEMPTS_DB", "coding_agent")
_COLLECTION = "failed_attempts"
_INDEX_ENSURED = False


def _collection():
    """Return the failed-attempts collection after a live ping."""
    from pymongo import ASCENDING, MongoClient

    from src.core.config import settings

    global _INDEX_ENSURED

    client = MongoClient(settings.mongo_url, serverSelectionTimeoutMS=2000)
    client.admin.command("ping")
    coll = client[_DB_NAME][_COLLECTION]
    if not _INDEX_ENSURED:
        coll.create_index([("ticket_id", ASCENDING), ("timestamp", ASCENDING)])
        _INDEX_ENSURED = True
    return coll


def save_failed_attempt(attempt: FailedAttempt) -> None:
    """Persist a FailedAttempt for this ticket (never expires)."""
    try:
        coll = _collection()
        coll.insert_one(dict(attempt))
    except Exception:
        pass


def get_failed_attempts(ticket_id: str) -> list[FailedAttempt]:
    """Return all failed attempts for ``ticket_id``, oldest first.

    Same contract as the former Redis store: callers (orchestrator / planner)
    still use ``attempts[-1]`` and ``attempts[-3:]``.
    """
    try:
        coll = _collection()
        docs: list[dict[str, Any]] = list(
            coll.find({"ticket_id": ticket_id}, {"_id": 0}).sort("timestamp", 1)
        )
        return docs  # type: ignore[return-value]
    except Exception:
        return []
