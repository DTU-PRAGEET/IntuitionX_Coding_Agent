"""LangGraph graph wiring — 3-route model (FULL / MICRO / EXIT) with HITL gates."""

from __future__ import annotations

from datetime import datetime, timezone

from langgraph.graph import END, StateGraph
from langgraph.types import interrupt

from src.agents.executor.agent import executor_node
from src.agents.orchestrator.agent import orchestrator_node
from src.agents.planner.agent import planner_node
from src.agents.research.agent import research_node
from src.core.checkpointing import get_checkpointer
from src.core.formatters import format_gate1_request, format_gate2_request
from src.core.orchestrator_context import normalize_routing_route
from src.core.state import AgentState, HumanApprovalGate
from src.tools.base import emit_event


def _record_gate(
    state: AgentState,
    gate_type: str,
    decision: dict,
) -> HumanApprovalGate:
    """Record Gate for this module."""
    status = decision.get("status", "REJECTED").upper()
    feedback = decision.get("feedback") or decision.get("notes")
    if status == "APPROVE":
        status = "APPROVED"
    elif status in ("REJECT", "REVISE") and feedback:
        status = "REVISE"
    elif status == "REVISE":
        status = "REVISE"
    gate = HumanApprovalGate(
        gate_id=f"{gate_type}_{state['session_id']}_{len(state.get('human_gates', []))}",
        gate_type=gate_type,  # type: ignore
        status=status,  # type: ignore
        feedback=feedback,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    from src.api.metrics_store import record_hag_decision

    record_hag_decision(gate_type, status)
    return gate


def research_hag_node(state: AgentState) -> AgentState:
    """HITL Gate 1 — post-research approval."""
    message = format_gate1_request(state)
    emit_event("gate_pause", {"gate": "GATE_1", "message": message[:500]})
    decision = interrupt(
        {
            "gate": "GATE_1",
            "message": message,
            "research_output": state.get("research_output"),
        }
    )
    gate = _record_gate(state, "RESEARCH", decision)
    gates = list(state.get("human_gates", []))
    gates.append(gate)
    return {**state, "human_gates": gates}


def planning_hag_node(state: AgentState) -> AgentState:
    """HITL Gate 2 — post-planning approval."""
    message = format_gate2_request(state)
    emit_event("gate_pause", {"gate": "GATE_2", "message": message[:500]})
    decision = interrupt(
        {
            "gate": "GATE_2",
            "message": message,
            "plan_output": state.get("plan_output"),
        }
    )
    gate = _record_gate(state, "PLANNING", decision)
    gates = list(state.get("human_gates", []))
    gates.append(gate)
    return {**state, "human_gates": gates}


def route_after_orchestrator(state: AgentState) -> str:
    """Route After Orchestrator for this module."""
    route = normalize_routing_route(state.get("routing_route", "FULL"))
    if route == "EXIT":
        return "exit"
    if route == "FULL":
        return "research"
    if route == "MICRO":
        return "planner"
    return "research"


def route_after_research_hag(state: AgentState) -> str:
    """Route After Research Hag for this module."""
    gates = state.get("human_gates", [])
    if not gates:
        return "reject"
    last = gates[-1]
    status = last.get("status", "REJECTED")
    if status == "APPROVED":
        return "planner"
    if status == "REVISE":
        return "research"
    return "reject"


def route_after_planning_hag(state: AgentState) -> str:
    """Route After Planning Hag for this module."""
    gates = state.get("human_gates", [])
    if not gates:
        return "reject"
    last = gates[-1]
    status = last.get("status", "REJECTED")
    if status == "APPROVED":
        return "executor"
    if status == "REVISE":
        return "planner"
    return "reject"


def route_after_planner(state: AgentState) -> str:
    """Route After Planner for this module."""
    return "planning_hag"


def build_graph(checkpointer=None):
    """Build and compile the LangGraph workflow."""
    graph = StateGraph(AgentState)

    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("research", research_node)
    graph.add_node("research_hag", research_hag_node)
    graph.add_node("planner", planner_node)
    graph.add_node("planning_hag", planning_hag_node)
    graph.add_node("executor", executor_node)

    graph.set_entry_point("orchestrator")

    graph.add_conditional_edges(
        "orchestrator",
        route_after_orchestrator,
        {
            "research": "research",
            "planner": "planner",
            "exit": END,
        },
    )

    graph.add_edge("research", "research_hag")
    graph.add_conditional_edges(
        "research_hag",
        route_after_research_hag,
        {
            "planner": "planner",
            "research": "research",
            "reject": END,
        },
    )

    graph.add_conditional_edges(
        "planner",
        route_after_planner,
        {
            "planning_hag": "planning_hag",
        },
    )

    graph.add_conditional_edges(
        "planning_hag",
        route_after_planning_hag,
        {
            "executor": "executor",
            "planner": "planner",
            "reject": END,
        },
    )

    graph.add_edge("executor", END)

    if checkpointer is None:
        checkpointer = get_checkpointer()
    return graph.compile(checkpointer=checkpointer)
