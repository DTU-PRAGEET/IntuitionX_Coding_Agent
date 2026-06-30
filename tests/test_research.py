"""Tests for Jira client/schema and Research agent wiring."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from langgraph.errors import GraphRecursionError

from src.agents.orchestrator.tools.jira_schema import JiraTicketRaw, enrich_security_ticket
from src.agents.research.memory import InMemoryResearchMemory
from src.agents.research.research_agent import _backfill_affected_files_from_hypotheses
from src.agents.research.tools.builder import build_research_tool_registry
from src.core.tool_permissions import get_allowed_tools
from src.core.schemas import AffectedFile, Hypothesis, ResearchReport
from src.integrations.cursor.gateway import DryRunCursorGateway
from tests.helpers import make_security_ticket


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


def test_security_ticket_has_enrichment():
    ticket = make_security_ticket("SEC-TEST-1")
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

    ticket = make_security_ticket("SEC-TEST-1")
    with (
        patch("src.agents.research.__main__.credentials_configured", return_value=True),
        patch("src.agents.research.__main__.fetch_jira_ticket", return_value=ticket),
    ):
        code = main(["--repo-path", str(tmp_path), "--dry-run"])
    assert code == 0
    out = capsys.readouterr().out
    payload = json.loads(out)
    assert payload["dry_run"] is True
    assert "cursor_search" in payload["tool_names"]
    assert payload["loaded_skill"] == "security_research"
    assert payload["skill_reason"] == "label_match"


def test_backfill_affected_files_from_hypothesis_evidence(tmp_path):
    student = tmp_path / "sqli" / "dao" / "student.py"
    student.parent.mkdir(parents=True)
    student.write_text("vulnerable\n", encoding="utf-8")
    user = tmp_path / "sqli" / "dao" / "user.py"
    user.write_text("md5\n", encoding="utf-8")

    report = ResearchReport(
        ticket_id="SPACE-129069",
        research_summary="Issues in dao layer",
        hypotheses=[
            Hypothesis(
                rank=1,
                hypothesis="SQL injection",
                evidence_strength="strong",
                evidence=["sqli/dao/student.py string interpolation"],
            ),
            Hypothesis(
                rank=2,
                hypothesis="Weak hashing",
                evidence_strength="moderate",
                evidence=["sqli/dao/user.py uses md5"],
            ),
        ],
        affected_files=[
            AffectedFile(
                path="sqli/app.py",
                reason="autoescape",
                evidence_strength="strong",
            )
        ],
        affected_modules=["sqli"],
        module_blast_radius=[],
        architectural_risk="high",
        issue_type_assessment="code_bug",
        flags=[],
    )

    updated = _backfill_affected_files_from_hypotheses(report, str(tmp_path))
    paths = [f.path for f in updated.affected_files]
    assert "sqli/app.py" in paths
    assert "sqli/dao/student.py" in paths
    assert "sqli/dao/user.py" in paths
    assert "affected_files_backfilled" in updated.flags


def test_run_research_parses_report(tmp_path):
    from src.agents.research.deep_runner import run_research

    ticket = make_security_ticket("PLAT-1234")
    fake_report = ResearchReport(
        ticket_id="PLAT-1234",
        research_summary="Issue in ExampleHandler",
        hypotheses=[
            {
                "rank": 1,
                "hypothesis": "Unparameterized query in handler",
                "evidence_strength": "strong",
                "evidence": ["grep match in handler.py"],
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
    assert report.ticket_id == "PLAT-1234"
    assert report.architectural_risk == "high"


def test_run_research_recursion_error_propagates(tmp_path):
    from src.agents.research.deep_runner import run_research

    ticket = make_security_ticket("SEC-TEST-1")
    mock_llm = MagicMock()
    mock_agent = MagicMock()
    mock_agent.invoke.side_effect = GraphRecursionError("limit reached")

    with patch("src.agents.research.research_agent.build_research_agent", return_value=(mock_agent, "test")):
        with pytest.raises(RuntimeError, match="Research agent failed"):
            run_research(ticket=ticket, repo_path=str(tmp_path), llm=mock_llm)
