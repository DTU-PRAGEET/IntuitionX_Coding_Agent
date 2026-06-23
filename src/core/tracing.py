"""LangSmith trace metadata helpers — thin wrappers for agent runners."""

from __future__ import annotations

from typing import Any

from src.core.state import AgentState

PROMPT_VERSIONS = {
    "orchestrator": "orch-v1.0",
    "research": "research-v2.0-deep",
    "planner": "planner-v1.0",
    "executor": "executor-v1.0",
}


def apply_trace_metadata(*, prompt_version: str, **kwargs: Any) -> None:
    """Attach optional metadata to the active LangSmith run (no-op if unavailable)."""
    try:
        from langsmith import get_current_run_tree

        run = get_current_run_tree()
        if run is not None:
            run.extra = {**(run.extra or {}), "prompt_version": prompt_version, **kwargs}
    except Exception:
        pass


def sync_trace_context_from_state(state: AgentState, *, prompt_version: str) -> None:
    """Sync session/ticket metadata into the active trace."""
    apply_trace_metadata(
        prompt_version=prompt_version,
        ticket_id=state.get("jira_ticket_id"),
        session_id=state.get("session_id"),
        routing_route=state.get("routing_route"),
    )
