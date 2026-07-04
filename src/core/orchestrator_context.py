"""Token-minimal context for orchestrator routing prompts only."""

from __future__ import annotations

from typing import Any

from src.core.logging import get_logger
from src.core.schemas import RoutingDecision
from src.core.skill_manager import get_skill_registry
from src.core.state import JiraTicket

_log = get_logger("orchestrator_context")

_DESCRIPTION_PREVIEW_MAX = 600


def list_orchestrator_security_skills() -> list[str]:
    """Canonical security skill stems the orchestrator may recommend."""
    return sorted(
        skill.name
        for skill in get_skill_registry().all_skills()
        if skill.module == "security"
    )


def _sanitize_recommended_skill(skill_name: str | None) -> str | None:
    """Drop orchestrator skill picks that are not in the registry."""
    if not skill_name:
        return None
    allowed = set(list_orchestrator_security_skills())
    normalized = skill_name.strip()
    if normalized not in allowed:
        _log.warning("orchestrator_invalid_security_skill", skill=normalized)
        return None
    return normalized


def sanitize_routing_decision(decision: RoutingDecision) -> RoutingDecision:
    """Validate orchestrator output before downstream agents consume it."""
    skill = _sanitize_recommended_skill(decision.recommended_security_skill)
    if skill == decision.recommended_security_skill:
        return decision
    return decision.model_copy(update={"recommended_security_skill": skill})


def apply_orchestrator_ticket_analysis(ticket: JiraTicket, decision: RoutingDecision) -> None:
    """Merge orchestrator LLM classification into the ticket used by research/planner."""
    history = ticket.setdefault("ticket_history_summary", {})
    history["orchestrator_analysis"] = {
        "vulnerability_category": decision.vulnerability_category,
        "recommended_security_skill": decision.recommended_security_skill,
    }

    if history.get("vulnerability_category_source") == "jira_field":
        return

    if decision.vulnerability_category:
        ticket["vulnerability_category"] = decision.vulnerability_category.strip()
    if decision.recommended_security_skill:
        history["recommended_security_skill"] = decision.recommended_security_skill


def build_orchestrator_routing_context(
    ticket: JiraTicket,
    *,
    user_prompt: str = "",
    repo_path: str | None = None,
    repo_profile: dict | None = None,
    previous_attempts: list | None = None,
    current_phase: str = "ORCHESTRATOR",
    token_budget: int = 150_000,
) -> dict[str, Any]:
    """Return only fields the routing LLM needs — no plans, steps, or full ticket body."""
    attempts = previous_attempts or []
    last = attempts[-1] if attempts else None
    priority = (
        ticket.get("priority_normalized")
        or ticket.get("priority_raw")
        or ticket.get("priority")
        or ""
    )

    description = (ticket.get("problem_statement") or ticket.get("title") or "").strip()
    if len(description) > _DESCRIPTION_PREVIEW_MAX:
        description = description[: _DESCRIPTION_PREVIEW_MAX - 3].rstrip() + "..."

    return {
        "ticket": {
            "issue_id": ticket.get("issue_id", ""),
            "title": (ticket.get("title") or "")[:200],
            "issue_type": ticket.get("issue_type", ""),
            "priority": priority,
            "labels": (ticket.get("labels") or [])[:12],
            "status": ticket.get("status", ""),
            "vulnerability_category": ticket.get("vulnerability_category") or "",
            "description_preview": description,
        },
        "security_skills": list_orchestrator_security_skills(),
        "user_prompt": user_prompt,
        "repository": {
            "repo_path": repo_path,
            "repo_type": (repo_profile or {}).get("repo_type") if repo_profile else None,
        },
        "retry_summary": {
            "previous_attempt_count": len(attempts),
            "last_failed_phase": last.get("phase_failed") if last else None,
            "last_error_summary": ((last.get("error_summary") or "")[:300] if last else None),
        },
        "current_phase": current_phase,
        "token_budget": token_budget,
    }
