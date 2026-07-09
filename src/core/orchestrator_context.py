"""Token-minimal context for orchestrator routing prompts only."""

from __future__ import annotations

import re
from typing import Any

from src.core.logging import get_logger
from src.core.schemas import RoutingDecision
from src.core.skill_manager import get_skill_registry
from src.core.state import JiraTicket

_log = get_logger("orchestrator_context")

_DESCRIPTION_PREVIEW_MAX = 600

_VALID_ROUTES = frozenset({"FULL", "MICRO", "EXIT"})

# Ticket text paths like sqli/dao/student.py or app/config.py
_NAMED_FILE_PATTERN = re.compile(
    r"([\w][\w./-]*\.(?:py|html|jinja2|yaml|yml|json|toml|properties|js|ts|tsx|jsx|go|java|rb|cs|php))",
    re.IGNORECASE,
)

_MICRO_NUDGE_MAX_NAMED_FILES = 6

_USER_ROUTE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:take|use|force|run|request)\s+(?:the\s+)?(FULL|MICRO|EXIT|SKIP_RESEARCH)\s+route\b",
        re.IGNORECASE,
    ),
    re.compile(r"\broute\s*[:=]\s*[\"']?(FULL|MICRO|EXIT|SKIP_RESEARCH)[\"']?\b", re.IGNORECASE),
    re.compile(r"\b(FULL|MICRO|EXIT|SKIP_RESEARCH)\s+route\b", re.IGNORECASE),
    re.compile(
        r"\brouting_route\s*[:=]\s*[\"']?(FULL|MICRO|EXIT|SKIP_RESEARCH)[\"']?\b",
        re.IGNORECASE,
    ),
)


def extract_named_file_paths(text: str) -> list[str]:
    """Return deduplicated file paths mentioned in ticket text."""
    if not text:
        return []
    seen: set[str] = set()
    ordered: list[str] = []
    for match in _NAMED_FILE_PATTERN.finditer(text):
        path = match.group(1).strip().lstrip("./")
        if path and path not in seen:
            seen.add(path)
            ordered.append(path)
    return ordered


def apply_locatability_micro_nudge(
    decision: RoutingDecision,
    ticket: JiraTicket,
    *,
    repo_path: str | None,
    previous_attempts: list | None = None,
) -> RoutingDecision:
    """Upgrade FULL → MICRO when ticket names files and repo is ready (LLM safety net)."""
    if normalize_routing_route(decision.routing_route) != "FULL":
        return decision
    if previous_attempts:
        return decision
    if not repo_path:
        return decision

    description = (ticket.get("problem_statement") or "").strip()
    title = (ticket.get("title") or "").strip()
    named_files = extract_named_file_paths(f"{title}\n{description}")
    if not named_files or len(named_files) > _MICRO_NUDGE_MAX_NAMED_FILES:
        return decision

    note = (
        f"[Locatability nudge: ticket names {len(named_files)} file(s) "
        f"({', '.join(named_files[:3])}{'...' if len(named_files) > 3 else ''}) "
        f"and repo is resolved → MICRO.]"
    )
    reasoning = f"{decision.reasoning} {note}".strip()
    _log.info(
        "orchestrator_micro_nudge",
        issue_id=ticket.get("issue_id"),
        named_files=named_files,
    )
    return decision.model_copy(
        update={
            "routing_route": "MICRO",
            "reasoning": reasoning,
        }
    )


def normalize_routing_route(route: str | None) -> str:
    """Map legacy SKIP_RESEARCH to MICRO; default unknown to FULL."""
    if not route:
        return "FULL"
    normalized = route.strip().upper()
    if normalized == "SKIP_RESEARCH":
        return "MICRO"
    if normalized in _VALID_ROUTES:
        return normalized
    return "FULL"


def parse_explicit_user_route(user_prompt: str) -> str | None:
    """Return FULL|MICRO|EXIT when user_prompt explicitly requests a pipeline route."""
    text = (user_prompt or "").strip()
    if not text:
        return None
    for pattern in _USER_ROUTE_PATTERNS:
        match = pattern.search(text)
        if match:
            return normalize_routing_route(match.group(1))
    return None


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
    route = normalize_routing_route(decision.routing_route)
    skill = _sanitize_recommended_skill(decision.recommended_security_skill)
    updates: dict[str, Any] = {}
    if route != decision.routing_route:
        updates["routing_route"] = route
    if skill != decision.recommended_security_skill:
        updates["recommended_security_skill"] = skill
    if not updates:
        return decision
    return decision.model_copy(update=updates)


def apply_user_route_override(
    decision: RoutingDecision,
    user_prompt: str,
) -> RoutingDecision:
    """Honor explicit route in user_prompt over LLM ticket-derived routing."""
    override = parse_explicit_user_route(user_prompt)
    if override is None:
        return decision
    if decision.routing_route == override:
        if "[User prompt explicitly requested" in (decision.reasoning or ""):
            return decision
        return decision.model_copy(
            update={
                "reasoning": (
                    f"{decision.reasoning} "
                    f"[User prompt explicitly requested {override} route; honoring override.]"
                ).strip()
            }
        )
    return decision.model_copy(
        update={
            "routing_route": override,
            "reasoning": (
                f"{decision.reasoning} "
                f"[User prompt explicitly requested {override} route; "
                f"honoring override over ticket-derived {decision.routing_route}.]"
            ).strip(),
        }
    )


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
