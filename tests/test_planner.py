"""Tests for Planner Agent (Level 4a)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage
from pydantic import ValidationError

from src.agents.planner.agent import (
    build_planner_prompt,
    load_research_report,
    run_planner,
)
from src.agents.planner.tools.builder import build_planner_tools
from src.core.tool_permissions import get_allowed_tools
from src.core.schemas import ExecutionPlan, ExecutionStep, ResearchReport

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPORT = REPO_ROOT / "src/agents/planner/fixtures/mock_research_report.json"
DEMO_FILE = REPO_ROOT / "src/agents/planner/fixtures/demo/UserSearchController.py"


def test_execution_step_syncs_legacy_to_canonical():
    step = ExecutionStep(
        step_id=1,
        action="EDIT",
        file="foo.py",
        target="replace sql",
        reason="fix injection",
        old_string="bad",
        new_string="good",
    )
    assert step.where == "foo.py"
    assert step.what == "replace sql"
    assert step.why == "fix injection"
    assert step.step_validate


def test_execution_step_edit_requires_patches():
    with pytest.raises(ValidationError, match="new_string"):
        ExecutionStep(
            step_id=1,
            action="EDIT",
            where="foo.py",
            what="fix",
            why="security",
            validate="grep clean",
        )


def test_load_mock_research_report():
    report = load_research_report(FIXTURE_REPORT)
    assert report.ticket_id == "SEC-4421"
    assert report.affected_files[0].path.endswith("UserSearchController.py")


def test_malformed_research_report_fails_loudly(tmp_path):
    bad = tmp_path / "bad_report.json"
    bad.write_text('{"ticket_id": "X"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid ResearchReport"):
        load_research_report(bad)


def test_planner_tools_read_grep_find_only(tmp_path):
    registry, names = build_planner_tools(str(tmp_path))
    try:
        assert names == get_allowed_tools("planner")
        assert registry.get("write") is not None  # registry has all L1
        assert "write" not in names
        assert "bash" not in names
    finally:
        registry.dispose()


def test_build_planner_prompt_includes_research():
    report = load_research_report(FIXTURE_REPORT)
    prompt = build_planner_prompt(report, str(REPO_ROOT))
    assert "SEC-4421" in prompt
    assert "research_output" in prompt
    assert "why, where, what, validate" in prompt


def test_run_planner_parses_plan_with_mock_llm(tmp_path):
    report = load_research_report(FIXTURE_REPORT)
    demo = tmp_path / "UserSearchController.py"
    demo.write_text('sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n', encoding="utf-8")
    report.affected_files[0].path = str(demo.relative_to(tmp_path))

    plan_json = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi via string concat",
        files_to_change=[str(demo.relative_to(tmp_path))],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where=str(demo.relative_to(tmp_path)),
                what="Locate search_users",
                why="Confirm vulnerable function",
                validate="File exists",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                where=str(demo.relative_to(tmp_path)),
                what="Parameterize SQL",
                why="Fix injection",
                validate="No f-string SQL",
                old_string='sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"',
                new_string='sql = "SELECT * FROM users WHERE name LIKE ?"',
                intent_marker="LIKE ?",
                change="Use parameterized query",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                where=str(demo.relative_to(tmp_path)),
                what="Confirm fix",
                why="Verify parameterized query",
                validate="grep for LIKE ?",
                intent_marker="LIKE ?",
            ),
        ],
        risk_level="high",
    )
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(plan_json.model_dump()))

    plan = run_planner(report, str(tmp_path), llm=mock_llm, verify=True)
    assert plan.ticket_id == "SEC-4421"
    edit_steps = [s for s in plan.execution_steps if s.action == "EDIT"]
    assert len(edit_steps) == 1
    assert edit_steps[0].old_string
    assert edit_steps[0].new_string


def test_run_planner_rejects_invalid_llm_output(tmp_path):
    report = load_research_report(FIXTURE_REPORT)
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content='{"ticket_id": "SEC-4421"}')

    with pytest.raises(ValueError, match="schema validation"):
        run_planner(report, str(tmp_path), llm=mock_llm, verify=False)


def test_run_planner_fills_missing_files_to_change_from_research(tmp_path):
    """LLM often omits files_to_change — coerce from research before validation."""
    report = load_research_report(FIXTURE_REPORT)
    demo = tmp_path / "sqli" / "dao" / "user.py"
    demo.parent.mkdir(parents=True)
    demo.write_text("SELECT 1\n", encoding="utf-8")
    rel = str(demo.relative_to(tmp_path))
    report.affected_files[0].path = rel

    payload = {
        "ticket_id": "SEC-4421",
        "root_hypothesis": "verify only",
        "execution_steps": [
            {
                "step_id": 1,
                "action": "READ",
                "where": rel,
                "what": "Read file",
                "why": "Confirm",
                "validate": "exists",
            }
        ],
        "write_tests": False,
        "create_files": False,
        "risk_level": "low",
        "flags": [],
        "implementation_constraints": [],
    }
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(payload))

    plan = run_planner(report, str(tmp_path), llm=mock_llm, verify=False)
    assert rel in plan.files_to_change


def test_planner_dry_run_cli():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.agents.planner",
            "--report",
            str(FIXTURE_REPORT),
            "--repo-path",
            str(REPO_ROOT),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["ticket_id"] == "SEC-4421"
    assert "[planner_cli]" in result.stderr or "[planner]" in result.stderr


def test_normalize_plan_replaces_dot_where_with_affected_file():
    from src.agents.planner.agent import _normalize_plan_paths, _validate_plan_executable

    report = ResearchReport(
        ticket_id="SEC-1",
        research_summary="x",
        hypotheses=[
            {"rank": 1, "hypothesis": "h", "evidence_strength": "strong", "evidence": ["e"]}
        ],
        affected_files=[
            {
                "path": "src/agents/planner/fixtures/demo/UserSearchController.py",
                "reason": "vuln",
                "evidence_strength": "strong",
            }
        ],
        affected_modules=[],
        module_blast_radius=[],
        architectural_risk="high",
        issue_type_assessment="code_bug",
        flags=[],
    )
    plan = ExecutionPlan(
        ticket_id="SEC-1",
        root_hypothesis="fix",
        files_to_change=[],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where=".",
                what="bad",
                why="bad",
                validate="x",
            ),
        ],
        risk_level="high",
    )
    plan = _normalize_plan_paths(plan, report, "/repo")
    _validate_plan_executable(plan, report, "/repo")
    assert plan.execution_steps[0].where.endswith("UserSearchController.py")
    assert plan.files_to_change


def test_validate_plan_rejects_dot_where_without_candidates():
    from src.agents.planner.agent import _validate_plan_executable

    report = ResearchReport(
        ticket_id="SEC-1",
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
    plan = ExecutionPlan(
        ticket_id="SEC-1",
        root_hypothesis="fix",
        files_to_change=[],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where=".",
                what="bad",
                why="bad",
                validate="x",
            ),
        ],
        risk_level="low",
    )
    with pytest.raises(ValueError, match="invalid where"):
        _validate_plan_executable(plan, report, "/repo")


def test_run_planner_normalizes_dot_where_from_llm(tmp_path):
    report = load_research_report(FIXTURE_REPORT)
    demo = tmp_path / "UserSearchController.py"
    demo.write_text('sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n', encoding="utf-8")
    rel = str(demo.relative_to(tmp_path))
    report.affected_files[0].path = rel

    plan_json = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi",
        files_to_change=[],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where=".",
                what="Locate file",
                why="Confirm",
                validate="exists",
            ),
        ],
        risk_level="high",
    )
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(plan_json.model_dump()))

    plan = run_planner(report, str(tmp_path), llm=mock_llm, verify=False)
    assert plan.execution_steps[0].where == rel
    assert rel in plan.files_to_change


def test_planner_verification_reads_demo_file():
    from src.pipeline.runner import _reset_mock_security_fixture

    _reset_mock_security_fixture(str(REPO_ROOT))
    report = load_research_report(FIXTURE_REPORT)
    plan_json = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi",
        files_to_change=["src/agents/planner/fixtures/demo/UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where="src/agents/planner/fixtures/demo/UserSearchController.py",
                what="Read vulnerable function",
                why="Ground patch",
                validate="read ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                where="src/agents/planner/fixtures/demo/UserSearchController.py",
                what="Fix SQL",
                why="Security",
                validate="No concat",
                old_string='sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"',
                new_string='sql = "SELECT * FROM users WHERE name LIKE ?"',
                intent_marker="LIKE ?",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                where="src/agents/planner/fixtures/demo/UserSearchController.py",
                what="Confirm fix",
                why="Verify",
                validate="grep for LIKE ?",
                intent_marker="LIKE ?",
            ),
        ],
        risk_level="high",
    )
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content=json.dumps(plan_json.model_dump()))

    plan = run_planner(report, str(REPO_ROOT), llm=mock_llm, verify=True)
    assert plan.ticket_id == "SEC-4421"
    assert not any("old_string not found" in f for f in plan.flags)
