"""Tests for end-to-end pipeline (Level 4c)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.executor.agent import ExecutionResult
from src.agents.orchestrator.tools.jira import mock_security_jira_ticket
from src.core.schemas import ExecutionPlan, ExecutionStep, ResearchReport
from src.core.tool_permissions import get_allowed_tools
from src.pipeline.runner import MOCK_SECURITY_TARGET, PipelineError, run_pipeline

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_get_allowed_tools_research():
    tools = get_allowed_tools("research")
    assert "cursor_search" in tools
    assert "web_search" in tools
    assert "grep" not in tools
    assert "write" not in tools


def test_get_allowed_tools_planner_executor():
    assert get_allowed_tools("planner") == ["read", "grep", "find"]
    assert get_allowed_tools("executor") == ["read", "bash", "git_worktree", "mr_create"]
    assert get_allowed_tools("orchestrator") == []


def test_builders_respect_permissions(tmp_path):
    from src.agents.executor.tools.builder import build_executor_tools
    from src.agents.planner.tools.builder import build_planner_tools
    from src.agents.research.tools.builder import build_research_tool_registry
    from src.integrations.cursor.gateway import DryRunCursorGateway

    gateway = DryRunCursorGateway(tmp_path)
    _, r_tools = build_research_tool_registry(str(tmp_path), gateway=gateway)
    p_reg, p_names = build_planner_tools(str(tmp_path))
    e_reg, e_names = build_executor_tools(str(tmp_path))
    try:
        assert set(p_names) == set(get_allowed_tools("planner"))
        assert set(e_names) == set(get_allowed_tools("executor"))
        r_names = {t.name for t in r_tools}
        for name in get_allowed_tools("research"):
            assert name in r_names
    finally:
        p_reg.dispose()
        e_reg.dispose()


def test_pipeline_dry_run_cli(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.pipeline",
            "--mock-security",
            "--repo-path",
            str(tmp_path),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["routing_route"] == "FULL"
    assert "[graph]" in result.stderr
    assert "node_start" in result.stderr
    assert "handoff" in result.stderr


def test_pipeline_full_mocked(tmp_path):
    ticket = mock_security_jira_ticket("SEC-4421")
    fake_report = ResearchReport(
        ticket_id="SEC-4421",
        research_summary="SQLi demo",
        hypotheses=[
            {
                "rank": 1,
                "hypothesis": "test",
                "evidence_strength": "strong",
                "evidence": ["demo"],
            }
        ],
        affected_files=[],
        affected_modules=["auth"],
        module_blast_radius=[],
        architectural_risk="high",
        issue_type_assessment="code_bug",
        flags=[],
    )
    fake_plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="fix demo",
        files_to_change=["demo.txt"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="demo.txt",
                what="create",
                why="demo",
                validate="exists",
                new_string="demo\n",
            ),
        ],
        create_files=True,
        risk_level="low",
    )
    fake_exec = ExecutionResult(
        ticket_id="SEC-4421",
        changed_files=["demo.txt"],
        steps_completed=1,
        step_results=[],
    )

    with (
        patch("src.pipeline.runner.mock_security_jira_ticket", return_value=ticket),
        patch("src.pipeline.runner.run_research", return_value=fake_report),
        patch("src.pipeline.runner.run_planner", return_value=fake_plan),
        patch("src.pipeline.runner.run_executor", return_value=fake_exec),
    ):
        result = run_pipeline("SEC-4421", str(tmp_path), mock_security=True)

    assert result.success
    assert result.plan is not None
    assert result.execution is not None
    assert result.execution.steps_completed == 1


def test_pipeline_stage_failed_research(tmp_path):
    ticket = mock_security_jira_ticket("SEC-4421")
    with (
        patch("src.pipeline.runner.mock_security_jira_ticket", return_value=ticket),
        patch("src.pipeline.runner.run_research", side_effect=RuntimeError("LLM down")),
    ):
        with pytest.raises(PipelineError, match="stage_failed=research"):
            run_pipeline("SEC-4421", str(tmp_path), mock_security=True)


def test_pipeline_stage_failed_planner(tmp_path):
    ticket = mock_security_jira_ticket("SEC-4421")
    fake_report = ResearchReport(
        ticket_id="SEC-4421",
        research_summary="x",
        hypotheses=[
            {"rank": 1, "hypothesis": "h", "evidence_strength": "weak", "evidence": []}
        ],
        affected_files=[],
        affected_modules=[],
        module_blast_radius=[],
        architectural_risk="low",
        issue_type_assessment="unknown",
        flags=[],
    )
    with (
        patch("src.pipeline.runner.mock_security_jira_ticket", return_value=ticket),
        patch("src.pipeline.runner.run_research", return_value=fake_report),
        patch(
            "src.pipeline.runner.build_mock_security_plan",
            side_effect=ValueError("bad plan"),
        ),
    ):
        with pytest.raises(PipelineError, match="stage_failed=planner"):
            run_pipeline("SEC-4421", str(tmp_path), mock_security=True)


def test_mock_security_uses_deterministic_plan(tmp_path):
    """Mock-security skips LLM planner and uses disk-grounded deterministic plan."""
    ticket = mock_security_jira_ticket("SEC-4421")
    demo_path = str(MOCK_SECURITY_TARGET)
    fake_report = ResearchReport(
        ticket_id="SEC-4421",
        research_summary="SQLi demo",
        hypotheses=[
            {
                "rank": 1,
                "hypothesis": "test",
                "evidence_strength": "strong",
                "evidence": ["demo"],
            }
        ],
        affected_files=[],
        affected_modules=["auth"],
        module_blast_radius=[],
        architectural_risk="high",
        issue_type_assessment="code_bug",
        flags=[],
    )
    fake_exec = ExecutionResult(
        ticket_id="SEC-4421",
        changed_files=[demo_path],
        steps_completed=3,
        step_results=[],
    )

    with (
        patch("src.pipeline.runner.mock_security_jira_ticket", return_value=ticket),
        patch("src.pipeline.runner.run_research", return_value=fake_report),
        patch("src.pipeline.runner.run_planner") as mock_planner,
        patch("src.pipeline.runner.run_executor", return_value=fake_exec),
    ):
        result = run_pipeline("SEC-4421", str(tmp_path), mock_security=True)

    assert result.success
    mock_planner.assert_not_called()
    assert result.plan is not None
    assert demo_path in result.plan.files_to_change
    assert any(step.action == "EDIT" for step in result.plan.execution_steps)


def test_pipeline_malformed_ticket_load_fails(tmp_path):
    with patch(
        "src.pipeline.runner.fetch_jira_ticket",
        side_effect=RuntimeError("Jira unavailable"),
    ):
        with pytest.raises(PipelineError, match="stage_failed=orchestrator"):
            run_pipeline("BAD-1", str(tmp_path), mock_security=False)
