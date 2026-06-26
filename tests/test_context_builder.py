"""Tests for ContextBuilder token-minimal handoffs."""

from __future__ import annotations

import json

from src.agents.orchestrator.tools.jira import mock_security_jira_ticket
from src.core.context_builder import ContextBuilder
from src.core.jira_compact import compact_jira_for_agent
from src.core.state import initial_state


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


def test_orchestrator_context_under_5kb():
    ticket = _long_ticket()
    state = initial_state("SEC-CTX-1")
    state["jira_ticket"] = ticket
    state["repo_path"] = "/tmp/demo-repo"
    state["repo_profile"] = {
        "repo_type": "python",
        "root_path": "/tmp/demo-repo",
        "modules": ["."],
        "package_roots": ["src"],
    }
    ctx = ContextBuilder.build("orchestrator", state)
    payload = ctx.model_dump_json(exclude_none=True)
    assert len(payload) < 5000, f"context too large: {len(payload)} bytes"
    assert "comments" not in payload
    assert "attachments" not in payload
    assert "ticket_history_summary" not in payload


def test_planner_context_omits_hypotheses():
    ticket = mock_security_jira_ticket("SEC-CTX-2")
    state = initial_state("SEC-CTX-2")
    state["jira_ticket"] = ticket
    state["research_output"] = {
        "ticket_id": "SEC-CTX-2",
        "research_summary": "Found SQLi in user search.",
        "hypotheses": [
            {
                "rank": 1,
                "hypothesis": "String concat in DAO",
                "evidence_strength": "strong",
                "evidence": ["grep hit"] * 20,
            }
        ],
        "affected_files": [
            {"path": "dao/user.py", "reason": "SQL concat", "evidence_strength": "strong"}
        ],
        "affected_modules": [],
        "module_blast_radius": [],
        "architectural_risk": "high",
        "issue_type_assessment": "code_bug",
        "flags": [],
    }
    ctx = ContextBuilder.build("planner", state)
    payload = ctx.model_dump(exclude_none=True)
    assert "hypotheses" not in json.dumps(payload)
    assert payload["research"]["affected_files"][0]["path"] == "dao/user.py"


def test_compact_jira_planner_omits_problem_statement():
    ticket = _long_ticket()
    compact = compact_jira_for_agent(ticket, "planner")
    assert compact is not None
    assert "problem_statement" not in compact
    assert "title" in compact


def test_research_context_truncates_problem():
    ticket = _long_ticket()
    compact = compact_jira_for_agent(ticket, "research")
    assert compact is not None
    assert len(compact["problem_statement"]) <= 2003
