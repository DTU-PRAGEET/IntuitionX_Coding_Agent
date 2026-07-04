"""Tests for end-to-end pipeline (Level 4c)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from src.agents.executor.agent import ExecutionResult
from src.core.schemas import ExecutionPlan, ExecutionStep, ResearchReport
from src.core.tool_permissions import get_allowed_tools
from src.core.schemas import RoutingDecision
from src.pipeline.runner import PipelineError, run_pipeline
from tests.helpers import make_security_ticket

REPO_ROOT = Path(__file__).resolve().parents[1]

_FULL_ROUTING = RoutingDecision(
    routing_route="FULL",
    risk_level="HIGH",
    reasoning="test routing",
)


def test_get_allowed_tools_research():
    """Test Get Allowed Tools Research for this module."""
    tools = get_allowed_tools("research")
    assert "cursor_search" in tools
    assert "web_search" in tools
    assert "grep" not in tools
    assert "write" not in tools


def test_get_allowed_tools_planner_executor():
    """Test Get Allowed Tools Planner Executor for this module."""
    assert get_allowed_tools("planner") == ["read", "grep", "find"]
    assert get_allowed_tools("executor") == ["read", "bash", "git_worktree", "mr_create"]
    assert get_allowed_tools("orchestrator") == []


def test_builders_respect_permissions(tmp_path):
    """Test Builders Respect Permissions for this module."""
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


def test_pipeline_dry_run(tmp_path):
    """Test Pipeline Dry Run for this module."""
    ticket = make_security_ticket("PLAT-1234")
    with (
        patch("src.pipeline.runner.fetch_jira_ticket", return_value=ticket),
        patch("src.pipeline.runner.resolve_routing", return_value=_FULL_ROUTING),
    ):
        result = run_pipeline("PLAT-1234", str(tmp_path), dry_run=True)

    assert result.success
    assert result.dry_run is True
    assert result.routing_route == "FULL"


def test_pipeline_full_mocked(tmp_path):
    """Test Pipeline Full Mocked for this module."""
    ticket = make_security_ticket("PLAT-1234")
    fake_report = ResearchReport(
        ticket_id="PLAT-1234",
        research_summary="Demo issue",
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
        ticket_id="PLAT-1234",
        root_hypothesis="fix demo",
        files_to_change=["demo.txt"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file="demo.txt",
                target="create",
                reason="demo",
                validate="exists",
                new_string="demo\n",
            ),
        ],
        create_files=True,
        risk_level="low",
    )
    fake_exec = ExecutionResult(
        ticket_id="PLAT-1234",
        changed_files=["demo.txt"],
        steps_completed=1,
        step_results=[],
    )

    with (
        patch("src.pipeline.runner.fetch_jira_ticket", return_value=ticket),
        patch("src.pipeline.runner.resolve_routing", return_value=_FULL_ROUTING),
        patch("src.pipeline.runner.run_research", return_value=fake_report),
        patch("src.pipeline.runner.run_planner", return_value=fake_plan),
        patch("src.pipeline.runner.run_executor", return_value=fake_exec),
    ):
        result = run_pipeline("PLAT-1234", str(tmp_path))

    assert result.success
    assert result.plan is not None
    assert result.execution is not None
    assert result.execution.steps_completed == 1


def test_pipeline_stage_failed_research(tmp_path):
    """Test Pipeline Stage Failed Research for this module."""
    ticket = make_security_ticket("PLAT-1234")
    with (
        patch("src.pipeline.runner.fetch_jira_ticket", return_value=ticket),
        patch("src.pipeline.runner.resolve_routing", return_value=_FULL_ROUTING),
        patch("src.pipeline.runner.run_research", side_effect=RuntimeError("LLM down")),
    ):
        with pytest.raises(PipelineError, match="stage_failed=research"):
            run_pipeline("PLAT-1234", str(tmp_path))


def test_pipeline_stage_failed_planner(tmp_path):
    """Test Pipeline Stage Failed Planner for this module."""
    ticket = make_security_ticket("PLAT-1234")
    fake_report = ResearchReport(
        ticket_id="PLAT-1234",
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
        patch("src.pipeline.runner.fetch_jira_ticket", return_value=ticket),
        patch("src.pipeline.runner.resolve_routing", return_value=_FULL_ROUTING),
        patch("src.pipeline.runner.run_research", return_value=fake_report),
        patch("src.pipeline.runner.run_planner", side_effect=ValueError("bad plan")),
    ):
        with pytest.raises(PipelineError, match="stage_failed=planner"):
            run_pipeline("PLAT-1234", str(tmp_path))


def test_pipeline_malformed_ticket_load_fails(tmp_path):
    """Test Pipeline Malformed Ticket Load Fails for this module."""
    with patch(
        "src.pipeline.runner.fetch_jira_ticket",
        side_effect=RuntimeError("Jira unavailable"),
    ):
        with pytest.raises(PipelineError, match="stage_failed=orchestrator"):
            run_pipeline("BAD-1", str(tmp_path))
