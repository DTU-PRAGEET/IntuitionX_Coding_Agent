# """StateUpdater — agents return artifacts; only this module mutates workflow fields."""

# from __future__ import annotations

# from datetime import datetime, timezone

# from src.core.schemas import RoutingDecision
# from src.core.state import AgentState, PlanOutput, ResearchOutput


# def apply_routing(state: AgentState, decision: RoutingDecision) -> AgentState:
#     now = datetime.now(timezone.utc).isoformat()
#     updates: AgentState = {
#         **state,
#         "routing_route": decision.routing_route,
#         "risk_level": decision.risk_level,
#         "current_phase": "ORCHESTRATOR",
#         "updated_at": now,
#     }
#     if decision.routing_route == "EXIT":
#         updates["current_phase"] = "EXIT"
#         updates["exit_message"] = decision.reasoning
#     return updates


# def apply_research(state: AgentState, output: ResearchOutput) -> AgentState:
#     now = datetime.now(timezone.utc).isoformat()
#     return {
#         **state,
#         "research_output": output,
#         "current_phase": "RESEARCH",
#         "updated_at": now,
#     }


# def apply_plan(state: AgentState, output: PlanOutput) -> AgentState:
#     now = datetime.now(timezone.utc).isoformat()
#     return {
#         **state,
#         "plan_output": output,
#         "current_phase": "PLANNING",
#         "updated_at": now,
#     }
