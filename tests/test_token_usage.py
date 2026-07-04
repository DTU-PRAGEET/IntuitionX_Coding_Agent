"""Tests for centralized LLM token usage tracking."""

from __future__ import annotations

from langchain_core.messages import AIMessage

from src.core.token_usage import (
    TokenUsageTracker,
    extract_token_usage,
    record_llm_response,
    start_pipeline_tracking,
    stop_pipeline_tracking,
)


def test_extract_token_usage_from_response_metadata():
    """Test Extract Token Usage From Response Metadata for this module."""
    msg = AIMessage(
        content="ok",
        response_metadata={
            "token_usage": {
                "prompt_tokens": 19,
                "completion_tokens": 9,
                "total_tokens": 28,
            }
        },
    )
    assert extract_token_usage(msg) == (19, 9, 28)


def test_extract_token_usage_from_usage_metadata():
    """Test Extract Token Usage From Usage Metadata for this module."""
    msg = AIMessage(
        content="ok",
        usage_metadata={"input_tokens": 11, "output_tokens": 5, "total_tokens": 16},
    )
    assert extract_token_usage(msg) == (11, 5, 16)


def test_extract_token_usage_from_response_metadata_usage_key():
    """Sprinklr/OpenAI responses may expose usage under response_metadata.usage."""
    msg = AIMessage(
        content="ok",
        response_metadata={
            "usage": {
                "prompt_tokens": 7,
                "completion_tokens": 3,
                "total_tokens": 10,
            }
        },
    )
    assert extract_token_usage(msg) == (7, 3, 10)


def test_tracker_to_dict():
    """Test Tracker To Dict for this module."""
    tracker = TokenUsageTracker()
    tracker.record("research", 100, 20, 120)
    tracker.record("planner", 50, 10, 60)
    data = tracker.to_dict()
    assert data["total_tokens"] == 180
    assert data["agents"]["research"]["call_count"] == 1


def test_global_tracker_visible_across_threads():
    """Test Global Tracker Visible Across Threads for this module."""
    import threading

    tracker = start_pipeline_tracking()
    try:

        def record_in_thread() -> None:
            """Record in thread."""
            record_llm_response(
                "planner",
                AIMessage(
                    content="x",
                    response_metadata={
                        "token_usage": {
                            "prompt_tokens": 4,
                            "completion_tokens": 1,
                            "total_tokens": 5,
                        }
                    },
                ),
            )

        thread = threading.Thread(target=record_in_thread)
        thread.start()
        thread.join()
        assert tracker.total_tokens == 5
    finally:
        stop_pipeline_tracking()


def test_tracker_aggregates_per_agent():
    """Test Tracker Aggregates Per Agent for this module."""
    tracker = TokenUsageTracker()
    tracker.record("planner", 100, 40, 140)
    tracker.record("research", 200, 80, 280)
    assert tracker.prompt_tokens == 300
    assert tracker.completion_tokens == 120
    assert tracker.total_tokens == 420


def test_pipeline_tracking_context():
    """Test Pipeline Tracking Context for this module."""
    tracker = start_pipeline_tracking()
    try:
        record_llm_response(
            "executor",
            AIMessage(content="x", response_metadata={"token_usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5}}),
        )
        assert tracker.total_tokens == 5
    finally:
        stop_pipeline_tracking()


def test_extract_usage_mapping_cursor_turn_ended():
    """Test Extract Usage Mapping Cursor Turn Ended for this module."""
    from src.core.token_usage import extract_usage_mapping

    usage = {"inputTokens": 100, "outputTokens": 40, "totalTokens": 140}
    assert extract_usage_mapping(usage) == (100, 40, 140)


def test_report_cursor_usage_with_tracing_agent():
    """Test Report Cursor Usage With Tracing Agent for this module."""
    from src.core.token_usage import (
        report_cursor_usage,
        reset_tracing_agent,
        set_tracing_agent,
        start_pipeline_tracking,
        stop_pipeline_tracking,
    )

    tracker = start_pipeline_tracking()
    token = set_tracing_agent("planner")
    try:
        report_cursor_usage({"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        assert tracker.agents["planner"].total_tokens == 15
    finally:
        reset_tracing_agent(token)
        stop_pipeline_tracking()
