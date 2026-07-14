"""Unit tests for MongoDB-backed failed-attempt store."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.memory.cache import get_failed_attempts, save_failed_attempt


def _sample_attempt(ticket_id: str = "SEC-1", ts: str = "2026-01-01T00:00:00+00:00") -> dict:
    return {
        "attempt_id": "sess-1",
        "ticket_id": ticket_id,
        "phase_failed": "IMPLEMENTATION",
        "error_type": "execution_error",
        "error_summary": "boom",
        "plan_hash": None,
        "plan_used": None,
        "timestamp": ts,
    }


def test_save_failed_attempt_inserts_into_mongo():
    coll = MagicMock()
    with patch("src.memory.cache._collection", return_value=coll):
        save_failed_attempt(_sample_attempt())  # type: ignore[arg-type]
    coll.insert_one.assert_called_once()
    doc = coll.insert_one.call_args[0][0]
    assert doc["ticket_id"] == "SEC-1"
    assert doc["error_summary"] == "boom"


def test_save_failed_attempt_swallows_errors():
    with patch("src.memory.cache._collection", side_effect=RuntimeError("down")):
        save_failed_attempt(_sample_attempt())  # type: ignore[arg-type]


def test_get_failed_attempts_returns_sorted_docs():
    a1 = _sample_attempt(ts="2026-01-01T00:00:00+00:00")
    a2 = _sample_attempt(ts="2026-02-01T00:00:00+00:00")
    cursor = MagicMock()
    cursor.sort.return_value = [a1, a2]
    coll = MagicMock()
    coll.find.return_value = cursor

    with patch("src.memory.cache._collection", return_value=coll):
        result = get_failed_attempts("SEC-1")

    coll.find.assert_called_once_with({"ticket_id": "SEC-1"}, {"_id": 0})
    cursor.sort.assert_called_once_with("timestamp", 1)
    assert result == [a1, a2]
    assert result[-1]["timestamp"] == a2["timestamp"]
    assert [r["error_summary"] for r in result[-3:]] == ["boom", "boom"]


def test_get_failed_attempts_returns_empty_on_error():
    with patch("src.memory.cache._collection", side_effect=RuntimeError("down")):
        assert get_failed_attempts("SEC-1") == []
