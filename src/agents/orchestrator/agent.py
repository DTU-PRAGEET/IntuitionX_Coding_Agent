"""Orchestrator — thin runner: integration I/O + prompt-driven routing."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from langchain_core.language_models import BaseChatModel
from langsmith import traceable

from src.core.ticket_repo import resolve_ticket_repo_path
from src.core.config import settings
from src.core.llm import TaskComplexity, get_llm
from src.core.logging import get_logger
from src.core.runner_utils import llm_content, invoke_tracked, parse_llm_json
from src.core.schemas import RoutingDecision
from src.core.state import AgentState, JiraTicket
from src.core.tracing import PROMPT_VERSIONS, apply_trace_metadata, sync_trace_context_from_state
from src.core.prompt_builder import PromptBuilder
from src.core.plan_validation import validate_execution_plan
from src.memory.cache import get_failed_attempts
from src.agents.orchestrator.tools.jira import fetch_jira_ticket, is_ticket_done, mock_security_jira_ticket
from src.agents.research.tools.repo_profile import profile_repository

_log = get_logger("orchestrator")

SKILLS_DIR = Path(__file__).parent / "skills"


def _resolve_repo_path(state: AgentState) -> tuple[str | None, str | None]:
    """Resolve local repo checkout and target branch from ticket or explicit override."""
    ticket = state.get("jira_ticket")
    if not ticket:
        explicit = state.get("repo_path")
        if explicit:
            return str(Path(explicit).resolve()), None
        if settings.default_repo_path and Path(settings.default_repo_path).exists():
            return str(Path(settings.default_repo_path).resolve()), None
        return None, None

    repo_path, _github_repo, target_branch = resolve_ticket_repo_path(
        ticket,
        explicit_path=state.get("repo_path"),
    )
    return repo_path, target_branch


@traceable(name="orchestrator_agent", run_type="chain")
def run_orchestrator_routing(
    ticket: JiraTicket,
    *,
    repo_path: str | None = None,
    repo_profile: dict | None = None,
    previous_attempts: list | None = None,
    user_prompt: str = "",
    llm: BaseChatModel | None = None,
) -> RoutingDecision:
    """Single-turn routing decision from orchestrator skills + ticket context."""
    apply_trace_metadata(prompt_version=PROMPT_VERSIONS["orchestrator"])
    llm = llm or get_llm(TaskComplexity.MEDIUM)
    prompt_state: AgentState = {
        "jira_ticket_id": ticket["issue_id"],
        "user_prompt": user_prompt,
        "context_image": None,
        "repo_path": repo_path,
        "git_target_branch": None,
        "jira_ticket": ticket,
        "repo_profile": repo_profile,
        "routing_route": None,
        "current_phase": "ORCHESTRATOR",
        "risk_level": None,
        "exit_message": None,
        "research_output": None,
        "plan_output": None,
        "execution_output": None,
        "human_gates": [],
        "previous_attempts": previous_attempts or [],
        "current_error_log": None,
        "retry_count": 0,
        "max_retries": 3,
        "self_healing_cycle_index": 0,
        "total_tokens_used": 0,
        "token_budget": settings.token_budget,
        "messages": [],
        "session_id": "standalone",
        "created_at": "",
        "updated_at": "",
        "prompt_version": "orch-v1.0",
        "pending_diff": None,
        "pending_worktree": None,
        "pending_branch": None,
    }
    routing_prompt, _manifest = PromptBuilder.build(
        "orchestrator",
        SKILLS_DIR,
        prompt_state,
        ticket=ticket,
        include_references=False,
        extra_instructions="Return ONLY the RoutingDecision JSON.",
    )
    try:
        response = invoke_tracked(llm, routing_prompt, agent="orchestrator")
        return parse_llm_json(llm_content(response), RoutingDecision)
    except Exception as e:
        # #region agent log
        from src.core.debug_log import agent_debug

        agent_debug(
            "orchestrator",
            "orchestrator/agent.py:run_orchestrator_routing",
            "routing_llm_error",
            {
                "error_type": type(e).__name__,
                "error": str(e)[:500],
                "llm_class": type(llm).__name__,
            },
            hypothesis_id="H1",
        )
        # #endregion
        _log.error("routing_failed", error=str(e))
        raise RuntimeError(f"Orchestrator routing failed for ticket={ticket['issue_id']!r}: {e}") from e


def orchestrator_node(state: AgentState) -> AgentState:
    now = datetime.now(timezone.utc).isoformat()
    ticket_id = state["jira_ticket_id"]
    previous = get_failed_attempts(ticket_id)
    user_prompt = state.get("user_prompt", "") or ""
    mock_security = "[mock-security]" in user_prompt
    if mock_security:
        user_prompt = user_prompt.replace("[mock-security]", "").strip()
        ticket = mock_security_jira_ticket(ticket_id)
    else:
        ticket = fetch_jira_ticket(ticket_id)

    # Integration gate: DONE ticket (not routing logic)
    if is_ticket_done(ticket):
        return {
            **state,
            "jira_ticket": ticket,
            "routing_route": "EXIT",
            "current_phase": "EXIT",
            "exit_message": f"Ticket {ticket_id} is already marked {ticket['status']}. No action taken.",
            "previous_attempts": previous,
            "updated_at": now,
        }

    repo_path, git_target_branch = _resolve_repo_path({**state, "jira_ticket": ticket})
    if mock_security and repo_path:
        from src.pipeline.runner import _reset_mock_security_fixture

        _reset_mock_security_fixture(repo_path)
    profile = profile_repository(repo_path) if repo_path else None

    try:
        sync_trace_context_from_state(state, prompt_version=PROMPT_VERSIONS["orchestrator"])
        if mock_security:
            from src.core.schemas import RoutingDecision

            decision = RoutingDecision(
                routing_route="FULL",
                risk_level="HIGH",
                reasoning="Mock security ticket — forced FULL pipeline for demo",
            )
        else:
            decision = run_orchestrator_routing(
                ticket,
                repo_path=repo_path,
                repo_profile=profile,
                previous_attempts=previous,
                user_prompt=user_prompt,
            )
    except Exception as e:
        return {
            **state,
            "jira_ticket": ticket,
            "repo_path": repo_path,
            "git_target_branch": git_target_branch,
            "repo_profile": profile,
            "routing_route": "EXIT",
            "current_phase": "EXIT",
            "exit_message": f"Orchestrator routing validation failed: {e}",
            "previous_attempts": previous,
            "updated_at": now,
        }

    updates = {
        **state,
        "jira_ticket": ticket,
        "user_prompt": user_prompt,
        "repo_path": repo_path,
        "git_target_branch": git_target_branch,
        "repo_profile": profile,
        "routing_route": decision.routing_route,
        "risk_level": decision.risk_level,
        "current_phase": "ORCHESTRATOR",
        "previous_attempts": previous,
        "token_budget": settings.token_budget,
        "max_retries": state.get("max_retries", 3),
        "prompt_version": "orch-v1.0",
        "updated_at": now,
    }
    if decision.routing_route == "EXIT":
        updates["current_phase"] = "EXIT"
        updates["exit_message"] = decision.reasoning
    return updates
