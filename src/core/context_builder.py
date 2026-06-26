# """ContextBuilder — AgentState → typed minimal AgentContext per phase."""

# from __future__ import annotations

# from typing import Literal

# from src.core.agent_context import (
#     AttemptSummary,
#     ExecutorContext,
#     OrchestratorContext,
#     PlanningContext,
#     RepoProfileCompact,
#     ResearchContext,
#     ResearchHandoff,
# )
# from src.core.jira_compact import compact_jira_for_agent
# from src.core.state import AgentState, ResearchOutput

# AgentName = Literal["orchestrator", "research", "planner", "executor"]


# def _summarize_attempts(state: AgentState) -> list[AttemptSummary]:
#     attempts = state.get("previous_attempts") or []
#     return [
#         AttemptSummary(
#             phase_failed=a.get("phase_failed") or "unknown",
#             error_type=a.get("error_type") or "unknown",
#             error_summary=(a.get("error_summary") or "")[:300],
#             plan_hash=a.get("plan_hash"),
#         )
#         for a in attempts[-5:]
#     ]


# def _repo_profile_compact(state: AgentState) -> RepoProfileCompact | None:
#     profile = state.get("repo_profile")
#     if not profile:
#         return None
#     return RepoProfileCompact(
#         repo_type=profile.get("repo_type", "unknown"),
#         modules=(profile.get("modules") or [])[:8],
#     )


# def _research_handoff(state: AgentState) -> ResearchHandoff:
#     ro: ResearchOutput | None = state.get("research_output")
#     if not ro:
#         return ResearchHandoff(ticket_id=state.get("jira_ticket_id") or "")
#     files = [
#         {
#             "path": f.get("path", ""),
#             "reason": (f.get("reason") or "")[:200],
#             "evidence_strength": f.get("evidence_strength", "moderate"),
#         }
#         for f in (ro.get("affected_files") or [])[:10]
#     ]
#     return ResearchHandoff(
#         ticket_id=ro.get("ticket_id") or state.get("jira_ticket_id") or "",
#         research_summary=(ro.get("research_summary") or "")[:800],
#         affected_files=files,
#         architectural_risk=ro.get("architectural_risk") or "medium",
#         flags=(ro.get("flags") or [])[:5],
#     )


# class ContextBuilder:
#     """Filter + summarize AgentState into token-minimal typed context."""

#     @staticmethod
#     def build(
#         agent: AgentName,
#         state: AgentState,
#     ) -> OrchestratorContext | ResearchContext | PlanningContext | ExecutorContext:
#         ticket = state.get("jira_ticket")
#         attempts = _summarize_attempts(state)
#         budget = state.get("token_budget", 150_000)
#         used = state.get("total_tokens_used", 0)

#         if agent == "orchestrator":
#             return OrchestratorContext(
#                 jira_ticket_id=state.get("jira_ticket_id") or "",
#                 user_prompt=state.get("user_prompt") or "",
#                 ticket=compact_jira_for_agent(ticket, "orchestrator") or {},
#                 repo_path=state.get("repo_path"),
#                 repo_profile=_repo_profile_compact(state),
#                 previous_attempts=attempts,
#                 current_error_log=(state.get("current_error_log") or "")[:500] or None,
#                 token_budget=budget,
#                 tokens_used=used,
#             )

#         if agent == "research":
#             return ResearchContext(
#                 jira_ticket_id=state.get("jira_ticket_id") or "",
#                 user_prompt=state.get("user_prompt") or "",
#                 ticket=compact_jira_for_agent(ticket, "research") or {},
#                 repo_path=state.get("repo_path"),
#                 repo_profile=_repo_profile_compact(state),
#                 previous_attempts=attempts,
#                 token_budget=budget,
#                 tokens_used=used,
#             )

#         if agent == "planner":
#             gate_feedback = None
#             gates = state.get("human_gates") or []
#             if gates and gates[-1].get("status") == "REVISE":
#                 gate_feedback = (gates[-1].get("feedback") or "")[:400]
#             return PlanningContext(
#                 jira_ticket_id=state.get("jira_ticket_id") or "",
#                 ticket=compact_jira_for_agent(ticket, "planner") or {},
#                 research=_research_handoff(state),
#                 routing_route=state.get("routing_route") or "FULL",
#                 repo_path=state.get("repo_path"),
#                 previous_attempts=attempts,
#                 gate_feedback=gate_feedback,
#                 token_budget=budget,
#                 tokens_used=used,
#             )

#         plan = state.get("plan_output") or {}
#         return ExecutorContext(
#             jira_ticket_id=state.get("jira_ticket_id") or "",
#             files_to_change=(plan.get("files_to_change") or [])[:20],
#             implementation_constraints=(plan.get("implementation_constraints") or [])[:10],
#             write_tests=bool(plan.get("write_tests")),
#             create_files=bool(plan.get("create_files")),
#             token_budget=budget,
#             tokens_used=used,
#         )
