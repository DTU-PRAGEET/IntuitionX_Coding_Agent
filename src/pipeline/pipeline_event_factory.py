"""Map LangGraph node outputs and interrupts to semantic pipeline events."""

from __future__ import annotations

from typing import Any

# HAG nodes emit no standalone event; interrupt payload covers the pause.
_HAG_NODES = frozenset({"research_hag", "planning_hag", "micro_confirm"})

_NODE_EVENT_MAP: dict[str, str] = {
    "orchestrator": "orchestrator_complete",
    "research": "research_complete",
    "planner": "planner_complete",
    "executor": "executor_complete",
}


class PipelineEventFactory:
    """Build event type + payload from graph observations."""

    @staticmethod
    def from_node(
        node_name: str,
        merged_state: dict[str, Any],
    ) -> tuple[str, dict[str, Any]] | None:
        """Return (event_type, payload) or None for pass-through nodes."""
        if node_name in _HAG_NODES:
            return None

        event_type = _NODE_EVENT_MAP.get(node_name)
        if event_type is None:
            return None

        if node_name == "orchestrator":
            payload = {
                "routing_route": merged_state.get("routing_route"),
                "repo_path": merged_state.get("repo_path"),
                "git_target_branch": merged_state.get("git_target_branch"),
                "current_phase": merged_state.get("current_phase"),
            }
        elif node_name == "research":
            payload = {"research_output": merged_state.get("research_output")}
        elif node_name == "planner":
            payload = {"plan_output": merged_state.get("plan_output")}
        elif node_name == "executor":
            payload = {"execution_output": merged_state.get("execution_output")}
        else:
            payload = {}

        return event_type, payload

    @staticmethod
    def from_interrupt(interrupt_payload: dict[str, Any]) -> tuple[str, dict[str, Any]]:
        """Map LangGraph interrupt value to a HITL pause event."""
        return "waiting_for_human_approval", dict(interrupt_payload)

    @staticmethod
    def lifecycle(
        event_type: str,
        payload: dict[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Wrap job lifecycle events (queued, running, completed, failed)."""
        return event_type, payload or {}
