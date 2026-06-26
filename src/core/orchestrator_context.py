"""Token-minimal context for orchestrator routing prompts only."""

from __future__ import annotations

from typing import Any

from src.core.state import JiraTicket


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

    return {
        "ticket": {
            "issue_id": ticket.get("issue_id", ""),
            "title": (ticket.get("title") or "")[:200],
            "issue_type": ticket.get("issue_type", ""),
            "priority": priority,
            "labels": (ticket.get("labels") or [])[:12],
            "status": ticket.get("status", ""),
        },
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
