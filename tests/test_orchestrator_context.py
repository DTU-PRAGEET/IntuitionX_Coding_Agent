"""Tests for token-minimal orchestrator routing context."""

from __future__ import annotations

import json

from src.agents.orchestrator.tools.jira import mock_security_jira_ticket
from src.core.orchestrator_context import build_orchestrator_routing_context


def _long_ticket():
    ticket = mock_security_jira_ticket("SEC-CTX-1")
    ticket["comments"] = [
        {"author": f"user{i}", "body": "x" * 500, "created": "2026-01-01"}
        for i in range(10)
    ]
    ticket["attachments"] = [{"name": f"file{i}.png", "type": "image/png"} for i in range(5)]
    ticket["ticket_history_summary"] = {"enrichment": "x" * 2000}
    ticket["problem_statement"] = "SQL injection. " + ("detail " * 800)
    return ticket


def test_orchestrator_routing_context_is_minimal():
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
    assert len(payload) < 2000, f"context too large: {len(payload)} bytes"
    assert "problem_statement" not in payload
    assert "execution_steps" not in payload
    assert "old_string" not in payload
    assert "package_roots" not in payload
    assert ctx["ticket"]["issue_id"] == "SEC-CTX-1"
    assert ctx["repository"]["repo_type"] == "python"
    assert ctx["retry_summary"]["previous_attempt_count"] == 1
    assert ctx["retry_summary"]["last_failed_phase"] == "EXECUTOR"
