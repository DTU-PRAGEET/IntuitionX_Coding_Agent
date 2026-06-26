"""Tests for Jira client/schema and Research agent wiring."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.agents.orchestrator.tools.jira import mock_security_jira_ticket
from src.agents.orchestrator.tools.jira_schema import JiraTicketRaw, enrich_security_ticket
from src.agents.research.memory import InMemoryResearchMemory
from src.agents.research.tools.builder import build_research_tool_registry
from src.core.tool_permissions import get_allowed_tools
from src.core.schemas import ResearchReport
from src.integrations.cursor.gateway import DryRunCursorGateway


def test_enrich_security_ticket_extracts_cve_and_severity():
    raw = JiraTicketRaw(
        key="SEC-1",
        summary="Critical SQL injection in AuthFilter",
        description="CVE-2024-99999 found in AuthFilter.java. Severity: critical.",
    )
    enrichment = enrich_security_ticket(raw)
    assert "CVE-2024-99999" in enrichment.cve_ids
    assert enrichment.severity == "critical"
    assert enrichment.affected_component == "AuthFilter"


def test_mock_security_ticket_has_enrichment():
    ticket = mock_security_jira_ticket("SEC-4421")
    enrichment = ticket["ticket_history_summary"]["security_enrichment"]
    assert enrichment["cve_ids"]
    assert "sql injection" in (enrichment.get("vulnerability_type") or "")


def test_research_tool_builder_includes_cursor_and_agent_tools(tmp_path):
    gateway = DryRunCursorGateway(tmp_path)
    _, tools = build_research_tool_registry(str(tmp_path), gateway=gateway)
    names = sorted(t.name for t in tools)
    research_allowed = get_allowed_tools("research")
    assert "cursor_search" in names
    assert "web_search" in names
    assert "repo_profile" in names
    assert "write" not in names
    assert "edit" not in names
    assert "grep" not in names
    for name in research_allowed:
        assert name in names


def test_in_memory_research_memory_snippet():
    mem = InMemoryResearchMemory()
    mem.record("tool_call", {"tool": "grep", "pattern": "foo"})
    snippet = mem.to_context_snippet()
    assert "tool_call" in snippet
    assert "grep" in snippet


def test_research_dry_run_cli(tmp_path, capsys):
    from src.agents.research.__main__ import main

    code = main(
        [
            "--mock-security",
            "--repo-path",
            str(tmp_path),
            "--dry-run",
        ]
    )
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert "cursor_search" in payload["tool_names"]
    assert payload["loaded_skill"] == "security_research"
    assert payload["skill_reason"] == "label_match"


def test_run_research_parses_report(tmp_path):
    from langchain_core.messages import AIMessage
    from src.agents.research.deep_runner import run_research

    ticket = mock_security_jira_ticket()
    fake_report = ResearchReport(
        ticket_id="SEC-4421",
        research_summary="SQLi in UserSearchController",
        hypotheses=[
            {
                "rank": 1,
                "hypothesis": "Unparameterized query in searchUsers",
                "evidence_strength": "strong",
                "evidence": ["grep match in UserSearchController.py"],
            }
        ],
        affected_files=[],
        affected_modules=["auth"],
        module_blast_radius=[],
        architectural_risk="high",
        issue_type_assessment="code_bug",
        flags=[],
    )
    mock_llm = MagicMock()
    mock_agent = MagicMock()
    mock_agent.invoke.return_value = {"structured_response": fake_report}

    with patch("src.agents.research.research_agent.build_research_agent", return_value=(mock_agent, "test")):
        report = run_research(ticket=ticket, repo_path=str(tmp_path), llm=mock_llm)
    assert report.ticket_id == "SEC-4421"
    assert report.architectural_risk == "high"


def test_run_research_mock_security_recursion_fallback(tmp_path):
    from langgraph.errors import GraphRecursionError
    from src.agents.research.deep_runner import run_research

    ticket = mock_security_jira_ticket()
    mock_llm = MagicMock()
    mock_agent = MagicMock()
    mock_agent.invoke.side_effect = GraphRecursionError("limit reached")

    with patch("src.agents.research.research_agent.build_research_agent", return_value=(mock_agent, "test")):
        report = run_research(ticket=ticket, repo_path=str(tmp_path), llm=mock_llm)

    assert report.ticket_id == "SEC-4421"
    assert "research_recursion_limit_fallback" in report.flags
    assert report.affected_files[0].path.endswith("UserSearchController.py")
