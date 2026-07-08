"""Tests for pipeline execution event store, factory, and sink."""

from __future__ import annotations

import json

import pytest

from src.api.execution_events import ExecutionEventStore, RedisEventSink, TERMINAL_EVENT_TYPES
from src.api.redis_store import DictRedisClient, events_key, events_seq_key
from src.pipeline.pipeline_event_factory import PipelineEventFactory


@pytest.fixture
def redis_client():
    """In-memory Redis substitute."""
    return DictRedisClient()


@pytest.fixture
def store(redis_client):
    """Event store backed by in-memory Redis."""
    return ExecutionEventStore(redis_client=redis_client)


def test_append_increments_seq(store):
    """Each append gets a monotonic sequence number."""
    e1 = store.append("job-a", "thread-1", "job_queued", {"ticket_id": "PLAT-1"})
    e2 = store.append("job-a", "thread-1", "job_running", {})
    assert e1.seq == 1
    assert e2.seq == 2
    assert e1.job_id == "job-a"
    assert e1.thread_id == "thread-1"
    assert e1.type == "job_queued"


def test_events_isolated_per_job(store):
    """Concurrent jobs write to disjoint Redis keys."""
    store.append("job-a", "thread-a", "job_queued", {})
    store.append("job-b", "thread-b", "job_queued", {})
    assert store.latest_seq("job-a") == 1
    assert store.latest_seq("job-b") == 1
    a_events = store.list_events("job-a").events
    b_events = store.list_events("job-b").events
    assert len(a_events) == 1
    assert len(b_events) == 1
    assert a_events[0].job_id == "job-a"
    assert b_events[0].job_id == "job-b"


def test_list_events_after_seq(store):
    """Polling returns only events after the cursor."""
    for i in range(3):
        store.append("job-x", "t", f"event_{i}", {"n": i})
    result = store.list_events("job-x", after_seq=1)
    assert len(result.events) == 2
    assert result.events[0].seq == 2
    assert result.latest_seq == 3


def test_is_terminal(store):
    """Terminal detection works for completed and failed events."""
    assert not store.is_terminal("job-z")
    store.append("job-z", "t", "job_running", {})
    assert not store.is_terminal("job-z")
    store.append("job-z", "t", "execution_completed", {})
    assert store.is_terminal("job-z")


def test_redis_event_sink_emits_to_bound_job(store):
    """Sink writes only to its bound job_id and thread_id."""
    sink = RedisEventSink("job-1", "thread-99", store)
    event = sink.emit("research_complete", {"research_output": {"ticket_id": "PLAT-1"}})
    assert event.job_id == "job-1"
    assert event.thread_id == "thread-99"
    stored = store.list_events("job-1").events
    assert len(stored) == 1
    assert stored[0].payload["research_output"]["ticket_id"] == "PLAT-1"


def test_resume_continues_same_event_log(store):
    """Initial run and resume append to the same job log."""
    sink1 = RedisEventSink("job-r", "thread-r", store)
    sink1.emit("research_complete", {"research_output": {}})
    sink2 = RedisEventSink("job-r", "thread-r", store)
    sink2.emit("planner_complete", {"plan_output": {}})
    events = store.list_events("job-r").events
    assert len(events) == 2
    assert events[0].thread_id == "thread-r"
    assert events[1].thread_id == "thread-r"


def test_factory_orchestrator_complete():
    """Orchestrator node maps to orchestrator_complete with routing fields."""
    state = {
        "routing_route": "FULL",
        "repo_path": "/tmp/repo",
        "git_target_branch": "develop",
        "current_phase": "ORCHESTRATOR",
    }
    result = PipelineEventFactory.from_node("orchestrator", state)
    assert result is not None
    event_type, payload = result
    assert event_type == "orchestrator_complete"
    assert payload["routing_route"] == "FULL"


def test_factory_research_complete():
    """Research node maps to research_complete with research_output."""
    state = {"research_output": {"ticket_id": "PLAT-2", "hypotheses": []}}
    result = PipelineEventFactory.from_node("research", state)
    assert result is not None
    event_type, payload = result
    assert event_type == "research_complete"
    assert payload["research_output"]["ticket_id"] == "PLAT-2"


def test_factory_hag_nodes_return_none():
    """HAG nodes do not emit standalone events."""
    assert PipelineEventFactory.from_node("research_hag", {}) is None
    assert PipelineEventFactory.from_node("planning_hag", {}) is None


def test_factory_interrupt():
    """Interrupt maps to waiting_for_human_approval."""
    payload = {
        "gate": "GATE_1",
        "message": "Approve research?",
        "research_output": {"ticket_id": "PLAT-3"},
    }
    event_type, event_payload = PipelineEventFactory.from_interrupt(payload)
    assert event_type == "waiting_for_human_approval"
    assert event_payload["gate"] == "GATE_1"
    assert event_payload["research_output"]["ticket_id"] == "PLAT-3"


def test_factory_lifecycle():
    """Lifecycle helper passes through event type and payload."""
    event_type, payload = PipelineEventFactory.lifecycle("job_queued", {"ticket_id": "X"})
    assert event_type == "job_queued"
    assert payload["ticket_id"] == "X"


def test_terminal_event_types_frozen():
    """Terminal types include completed and failed."""
    assert "execution_completed" in TERMINAL_EVENT_TYPES
    assert "execution_failed" in TERMINAL_EVENT_TYPES


def test_redis_keys_use_job_id(redis_client, store):
    """Events are stored under job-scoped Redis keys."""
    store.append("uuid-job", "t", "job_queued", {})
    assert redis_client.lrange(events_key("uuid-job"), 0, -1)
    assert redis_client.incr(events_seq_key("uuid-job")) == 2
