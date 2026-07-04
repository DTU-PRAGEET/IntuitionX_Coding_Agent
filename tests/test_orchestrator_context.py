"""Tests for token-minimal orchestrator routing context."""

from __future__ import annotations

import json

from src.core.orchestrator_context import (
    apply_orchestrator_ticket_analysis,
    build_orchestrator_routing_context,
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
