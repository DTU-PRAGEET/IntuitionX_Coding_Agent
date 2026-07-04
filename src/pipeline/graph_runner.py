"""Shared LangGraph runner — pauses at HITL gates (same semantics as Chainlit)."""

from __future__ import annotations

from typing import Any

from langgraph.types import Command

from src.core.checkpointing import ensure_async_checkpointer
from src.core.graph import build_graph
from src.core.logging import get_logger
from src.core.state import AgentState, initial_state
from src.core.token_usage import start_pipeline_tracking, stop_pipeline_tracking
from src.pipeline.graph_outcome import GraphRunOutcome

_log = get_logger("graph_runner")


def _apply_state(final_state: dict[str, Any] | None, node_output: dict[str, Any]) -> dict[str, Any]:
    """Apply State.
    Supports the surrounding module workflow.
    """
    return {**(final_state or {}), **node_output}


async def _stream_graph_segment(
    graph,
    config: dict[str, Any],
    state: AgentState | Command,
) -> tuple[GraphRunOutcome | None, AgentState | Command | None]:
    """Run until completion or first interrupt. Returns resume input when paused."""
    final_state: dict[str, Any] | None = None
    interrupted = False
    interrupt_payload: dict[str, Any] | None = None

    async for event in graph.astream(state, config=config, stream_mode="updates"):
        for node_name, node_output in event.items():
            if node_name == "__interrupt__":
                interrupted = True
                interrupts = node_output
                if interrupts:
                    first = interrupts[0]
                    interrupt_payload = first.value if hasattr(first, "value") else first
                break
            if isinstance(node_output, dict):
                final_state = _apply_state(final_state, node_output)
                _log.info("graph_node_complete", node=node_name)

    if interrupted and interrupt_payload:
        gate = interrupt_payload.get("gate", "GATE")
        return (
            GraphRunOutcome(kind="paused", state=final_state or {}, gate=gate),
            None,
        )

    merged = final_state or {}
    if merged.get("current_error_log"):
        return GraphRunOutcome(
            kind="failed",
            state=merged,
            error=str(merged["current_error_log"])[:4000],
        ), None

    return GraphRunOutcome(kind="completed", state=merged), None


async def run_graph_until_pause(
    *,
    jira_ticket_id: str,
    thread_id: str,
    job_id: str,
    user_prompt: str = "",
    repo_path: str | None = None,
) -> GraphRunOutcome:
    """Start or continue a pipeline run until done, failure, or HITL pause."""
    checkpointer = await ensure_async_checkpointer()
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}
    state: AgentState | Command = initial_state(
        jira_ticket_id=jira_ticket_id,
        user_prompt=user_prompt,
        repo_path=repo_path,
        job_id=job_id,
    )

    tracker = start_pipeline_tracking()
    try:
        outcome, _ = await _stream_graph_segment(graph, config, state)
        return outcome or GraphRunOutcome(kind="failed", state={}, error="empty graph result")
    except Exception as exc:
        _log.exception("graph_run_failed", ticket=jira_ticket_id, thread_id=thread_id)
        return GraphRunOutcome(kind="failed", state={}, error=str(exc)[:4000])
    finally:
        active = stop_pipeline_tracking() or tracker
        active.log_summary()


async def resume_graph_until_pause(
    thread_id: str,
    decision: dict[str, Any],
) -> GraphRunOutcome:
    """Resume a paused graph from POST /approve/{thread_id}."""
    checkpointer = await ensure_async_checkpointer()
    graph = build_graph(checkpointer=checkpointer)
    config = {"configurable": {"thread_id": thread_id}}

    tracker = start_pipeline_tracking()
    try:
        outcome, _ = await _stream_graph_segment(graph, config, Command(resume=decision))
        return outcome or GraphRunOutcome(kind="failed", state={}, error="empty resume result")
    except Exception as exc:
        _log.exception("graph_resume_failed", thread_id=thread_id)
        return GraphRunOutcome(kind="failed", state={}, error=str(exc)[:4000])
    finally:
        active = stop_pipeline_tracking() or tracker
        active.log_summary()
