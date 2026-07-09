"""Tests for token-minimal orchestrator routing context."""

from __future__ import annotations

import json

from src.core.orchestrator_context import (
    apply_locatability_micro_nudge,
    apply_orchestrator_ticket_analysis,
    apply_user_route_override,
    build_orchestrator_routing_context,
    extract_named_file_paths,
    normalize_routing_route,
    parse_explicit_user_route,
    sanitize_routing_decision,
)
from src.core.schemas import RoutingDecision
from tests.helpers import make_security_ticket


def _long_ticket():
    """Long Ticket for this module."""
    ticket = make_security_ticket("SEC-CTX-1")
    ticket["comments"] = [
        {"author": f"user{i}", "body": "x" * 500, "created": "2026-01-01"}
        for i in range(10)
    ]
    ticket["attachments"] = [{"name": f"file{i}.png", "type": "image/png"} for i in range(5)]
    ticket["ticket_history_summary"] = {"enrichment": "x" * 2000}
    ticket["problem_statement"] = "SQL injection. " + ("detail " * 800)
    return ticket


def test_orchestrator_routing_context_is_minimal():
    """Test Orchestrator Routing Context Is Minimal for this module."""
    ticket = _long_ticket()
    ctx = build_orchestrator_routing_context(
        ticket,
        user_prompt="fix sqli",
        repo_path="/tmp/demo-repo",
        repo_profile={"repo_type": "python", "modules": ["."], "package_roots": ["src"]},
        previous_attempts=[
            {
                "attempt_id": "a1",
                "ticket_id": "SEC-CTX-1",
                "phase_failed": "EXECUTOR",
                "error_type": "edit_failed",
                "error_summary": "patch did not apply",
                "plan_hash": "abc",
                "plan_used": {
                    "execution_steps": [{"action": "edit", "old_string": "x" * 5000}],
                },
                "timestamp": "2026-01-01",
            }
        ],
        token_budget=150_000,
    )
    payload = json.dumps(ctx)
    assert len(payload) < 3500, f"context too large: {len(payload)} bytes"
    assert "description_preview" in ctx["ticket"]
    assert "security_skills" in ctx
    assert len(ctx["security_skills"]) >= 5
    assert "execution_steps" not in payload
    assert "old_string" not in payload
    assert "package_roots" not in payload
    assert ctx["ticket"]["issue_id"] == "SEC-CTX-1"
    assert ctx["repository"]["repo_type"] == "python"
    assert ctx["retry_summary"]["previous_attempt_count"] == 1
    assert ctx["retry_summary"]["last_failed_phase"] == "EXECUTOR"


def test_sanitize_routing_decision_drops_unknown_security_skill():
    """Test Sanitize Routing Decision Drops Unknown Security Skill for this module."""
    decision = RoutingDecision(
        routing_route="FULL",
        risk_level="HIGH",
        reasoning="security ticket",
        recommended_security_skill="security/rce",
    )
    sanitized = sanitize_routing_decision(decision)
    assert sanitized.recommended_security_skill is None


def test_apply_orchestrator_ticket_analysis_overrides_heuristic_category():
    """Test Apply Orchestrator Ticket Analysis Overrides Heuristic Category for this module."""
    ticket = make_security_ticket("SEC-ORCH-1")
    ticket["vulnerability_category"] = "rce"
    ticket["ticket_history_summary"] = {"vulnerability_category_source": "unset"}
    decision = RoutingDecision(
        routing_route="FULL",
        risk_level="HIGH",
        reasoning="committed secrets in config",
        vulnerability_category="Cryptographic Failures",
        recommended_security_skill="cryptographic-failures",
    )
    apply_orchestrator_ticket_analysis(ticket, decision)
    assert ticket["vulnerability_category"] == "Cryptographic Failures"
    assert (
        ticket["ticket_history_summary"]["orchestrator_analysis"]["recommended_security_skill"]
        == "cryptographic-failures"
    )


def test_apply_orchestrator_ticket_analysis_respects_jira_field():
    """Test Apply Orchestrator Ticket Analysis Respects Jira Field for this module."""
    ticket = make_security_ticket("SEC-JIRA-1")
    ticket["vulnerability_category"] = "Injection"
    ticket["ticket_history_summary"] = {"vulnerability_category_source": "jira_field"}
    decision = RoutingDecision(
        routing_route="FULL",
        risk_level="HIGH",
        reasoning="test",
        vulnerability_category="Cryptographic Failures",
        recommended_security_skill="cryptographic-failures",
    )
    apply_orchestrator_ticket_analysis(ticket, decision)
    assert ticket["vulnerability_category"] == "Injection"


def test_normalize_routing_route_maps_skip_research_to_micro():
    assert normalize_routing_route("SKIP_RESEARCH") == "MICRO"
    assert normalize_routing_route("MICRO") == "MICRO"


def test_parse_explicit_user_route():
    assert parse_explicit_user_route("please take MICRO route") == "MICRO"
    assert parse_explicit_user_route("route: FULL") == "FULL"
    assert parse_explicit_user_route("use SKIP_RESEARCH route") == "MICRO"
    assert parse_explicit_user_route("fix the sqli") is None


def test_apply_user_route_override():
    base = RoutingDecision(
        routing_route="FULL",
        risk_level="MEDIUM",
        reasoning="symptom only",
    )
    overridden = apply_user_route_override(base, "take MICRO route")
    assert overridden.routing_route == "MICRO"
    assert "User prompt explicitly requested MICRO" in overridden.reasoning


def test_routing_decision_coerces_skip_research():
    decision = RoutingDecision(
        routing_route="SKIP_RESEARCH",
        risk_level="LOW",
        reasoning="legacy",
    )
    assert decision.routing_route == "MICRO"


def test_extract_named_file_paths():
    text = (
        "SQLi in `sqli/dao/student.py` and XSS in app/templates/reviews.html. "
        "Also see config/dev.yaml."
    )
    paths = extract_named_file_paths(text)
    assert paths == [
        "sqli/dao/student.py",
        "app/templates/reviews.html",
        "config/dev.yaml",
    ]


def test_apply_locatability_micro_nudge_upgrades_full_to_micro():
    ticket = make_security_ticket("SPACE-129069")
    ticket["problem_statement"] = (
        "Fix SQL injection in sqli/dao/student.py where names are string-formatted into INSERT."
    )
    decision = RoutingDecision(
        routing_route="FULL",
        risk_level="HIGH",
        reasoning="security ticket",
    )
    nudged = apply_locatability_micro_nudge(
        decision,
        ticket,
        repo_path="/tmp/demo-repo",
        previous_attempts=[],
    )
    assert nudged.routing_route == "MICRO"
    assert "Locatability nudge" in nudged.reasoning


def test_apply_locatability_micro_nudge_skips_without_named_files():
    ticket = make_security_ticket("SEC-NOFILE-1")
    ticket["problem_statement"] = "Users report SQL injection when adding students."
    decision = RoutingDecision(
        routing_route="FULL",
        risk_level="HIGH",
        reasoning="symptom only",
    )
    nudged = apply_locatability_micro_nudge(
        decision,
        ticket,
        repo_path="/tmp/demo-repo",
        previous_attempts=[],
    )
    assert nudged.routing_route == "FULL"
