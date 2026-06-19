"""Structured context assembly — token-minimal handoffs between phases."""

from __future__ import annotations

import json
from typing import Any

from src.core.state import AgentState


def transform_context(phase: str, state: AgentState) -> dict[str, Any]:
    """Build minimal structured context for each agent phase."""
    base = {
        "jira_ticket_id": state.get("jira_ticket_id"),
        "user_prompt": state.get("user_prompt", ""),
        "token_budget": state.get("token_budget", 150_000),
        "total_tokens_used": state.get("total_tokens_used", 0),
    }

    if phase == "orchestrator":
        return {
            **base,
            "jira_ticket": state.get("jira_ticket"),
            "previous_attempts": _summarize_attempts(state.get("previous_attempts", [])),
            "current_error_log": state.get("current_error_log"),
            "repo_path": state.get("repo_path"),
            "repo_profile": state.get("repo_profile"),
        }

    if phase == "research":
        return {
            **base,
            "jira_ticket": state.get("jira_ticket"),
            "repo_path": state.get("repo_path"),
            "repo_profile": state.get("repo_profile"),
            "previous_attempts": _summarize_attempts(state.get("previous_attempts", [])),
        }

    if phase == "planner":
        return {
            **base,
            "jira_ticket": {
                "issue_id": (state.get("jira_ticket") or {}).get("issue_id"),
                "acceptance_criteria": (state.get("jira_ticket") or {}).get("acceptance_criteria"),
                "title": (state.get("jira_ticket") or {}).get("title"),
            },
            "research_output": state.get("research_output"),
            "routing_route": state.get("routing_route"),
            "previous_attempts": _summarize_attempts(state.get("previous_attempts", [])),
            "repo_path": state.get("repo_path"),
        }

    if phase == "executor":
        plan = state.get("plan_output") or {}
        return {
            **base,
            "files_to_change": plan.get("files_to_change", []),
            "implementation_constraints": plan.get("implementation_constraints", []),
            "write_tests": plan.get("write_tests", False),
            "create_files": plan.get("create_files", False),
        }

    return base


def _summarize_attempts(attempts: list) -> list[dict]:
    """Compact previous attempts for prompt injection."""
    return [
        {
            "phase_failed": a.get("phase_failed"),
            "error_type": a.get("error_type", "unknown"),
            "error_summary": (a.get("error_summary") or "")[:300],
            "plan_hash": a.get("plan_hash"),
        }
        for a in attempts[-5:]
    ]


def build_prompt(skills: str, phase: str, state: AgentState, extra: dict | None = None) -> str:
    ctx = transform_context(phase, state)
    if extra:
        ctx.update(extra)
    return f"{skills}\n\n## CONTEXT\n{json.dumps(ctx, indent=2, default=str)}\n"
