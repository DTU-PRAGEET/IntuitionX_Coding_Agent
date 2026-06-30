"""Basic tests for Sprinklr Jira Solver core modules."""

from __future__ import annotations

import os
import unittest
from unittest.mock import patch

import pytest

from src.core.schemas import ExecutionPlan, ResearchReport, RoutingDecision, ToolResult
from src.core.state import initial_state
from src.agents.research.tools.file_discovery import extract_anchors, rank_candidate_files
from src.agents.orchestrator.tools.jira import JiraCredentialsError, fetch_jira_ticket, is_ticket_done
from src.agents.research.tools.repo_profile import profile_repository
from tests.helpers import make_bug_ticket

_JIRA_ENV_KEYS = ("JIRA_SERVER_URL", "JIRA_EMAIL", "JIRA_API_TOKEN")


def _without_jira_env():
    saved = {k: os.environ.pop(k, None) for k in _JIRA_ENV_KEYS}
    return saved


def _restore_env(saved: dict[str, str | None]) -> None:
    for k, v in saved.items():
        if v is not None:
            os.environ[k] = v


def test_initial_state():
    s = initial_state("PLAT-1234", "fix bug")
    assert s["jira_ticket_id"] == "PLAT-1234"
    assert s["current_phase"] == "INIT"
    assert s["routing_route"] is None


def test_fetch_jira_raises_without_credentials():
    saved = _without_jira_env()
    try:
        with patch("src.agents.orchestrator.tools.jira.credentials_configured", return_value=False):
            with pytest.raises(JiraCredentialsError, match="Jira credentials not configured"):
                fetch_jira_ticket("PLAT-9999")
    finally:
        _restore_env(saved)


def test_routing_decision_schema():
    d = RoutingDecision(routing_route="FULL", risk_level="MEDIUM", reasoning="test")
    assert d.routing_route == "FULL"


def test_repo_profiler_unknown(tmp_path):
    profile = profile_repository(str(tmp_path))
    assert profile["repo_type"] == "unknown"


def test_extract_anchors():
    ticket = make_bug_ticket("PLAT-1")
    anchors = extract_anchors(ticket, "")
    assert any("PaymentService" in a for a in anchors)


def test_graph_builds():
    from src.core.graph import build_graph

    g = build_graph()
    assert g is not None


def test_tool_result_schema():
    r = ToolResult(output="ok", exit_code=0, is_error=False, latency_ms=10, tool_name="read")
    assert r.to_llm_string()
    assert "ok" in r.to_llm_string()


def test_token_budget_default():
    s = initial_state("PLAT-1")
    assert s["token_budget"] == 150_000


class TestCore(unittest.TestCase):
    def test_initial_state(self):
        test_initial_state()

    def test_fetch_jira_raises_without_credentials(self):
        test_fetch_jira_raises_without_credentials()

    def test_routing_decision_schema(self):
        test_routing_decision_schema()

    def test_repo_profiler_unknown(self):
        import tempfile
        profile = profile_repository(tempfile.mkdtemp())
        self.assertEqual(profile["repo_type"], "unknown")

    def test_extract_anchors(self):
        test_extract_anchors()

    def test_graph_builds(self):
        test_graph_builds()

    def test_tool_result_schema(self):
        test_tool_result_schema()

    def test_execution_step_patches(self):
        from src.core.schemas import ExecutionStep

        step = ExecutionStep(
            step_id=1,
            action="EDIT",
            file="foo.py",
            target="bar",
            reason="test",
            old_string="a",
            new_string="b",
        )
        self.assertEqual(step.old_string, "a")


if __name__ == "__main__":
    unittest.main()
