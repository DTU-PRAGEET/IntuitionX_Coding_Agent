"""Tests for semantic Planner Agent."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from pydantic import ValidationError

from src.agents.planner.agent import (
    _normalize_plan_paths,
    _validate_plan_executable,
    load_research_report,
    run_planner,
    synthesize_micro_research_output,
)
from src.agents.planner.tools.builder import build_planner_tools
from src.core.tool_permissions import get_allowed_tools
from src.core.schemas import ExecutionPlan, ExecutionStep, ResearchReport
from src.core.plan_validation import validate_instruction_edits
from src.integrations.cursor.gateway import CursorGatewayResult
from src.integrations.cursor.semantic_plan_runner import build_semantic_plan_prompt

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_REPORT = REPO_ROOT / "src/agents/planner/fixtures/mock_research_report.json"


@pytest.fixture(autouse=True)
def _clear_cursor_api_key_for_planner_tests(monkeypatch):
    """Prevent accidental live Cursor calls during planner unit tests."""
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)


def _semantic_edit_step(**overrides) -> dict:
    base = {
        "step_id": 2,
        "action": "EDIT",
        "file": "handler.py",
        "target": "Parameterize SQL",
        "reason": "Fix injection",
        "validate": "grep for LIKE ?",
        "target_type": "assignment",
        "target_name": "sql",
        "approximate_line": 1,
        "instruction": "Replace f-string SQL with parameterized query using LIKE ?",
        "intent_marker": "LIKE ?",
        "change": "Use parameterized query",
    }
    base.update(overrides)
    return base


def test_execution_step_maps_legacy_where_keys():
    """Legacy where/what/why keys still normalize."""
    step = ExecutionStep.model_validate(
        {
            "step_id": 1,
            "action": "EDIT",
            "where": "foo.py",
            "what": "replace sql",
            "why": "fix injection",
            "instruction": "fix it with acceptance checklist",
            "intent_marker": "FIXED",
            "target_type": "block",
            "target_name": "fix_region",
        }
    )
    assert step.file == "foo.py"
    assert step.target == "replace sql"
    assert step.reason == "fix injection"


def test_execution_step_rejects_legacy_patch_fields():
    with pytest.raises(ValidationError, match="legacy patch"):
        ExecutionStep(
            step_id=1,
            action="EDIT",
            file="foo.py",
            target="fix",
            reason="security",
            validate="grep clean",
            instruction="fix it",
            intent_marker="FIXED",
            target_type="block",
            target_name="fix_region",
            old_string="bad",
            new_string="good",
        )


def test_execution_step_edit_requires_instruction_and_marker():
    """Instruction EDIT requires instruction and intent_marker."""
    with pytest.raises(ValidationError, match="instruction"):
        ExecutionStep(
            step_id=1,
            action="EDIT",
            file="foo.py",
            target="fix",
            reason="security",
            validate="grep clean",
            intent_marker="FIXED",
        )


def test_load_mock_research_report():
    report = load_research_report(FIXTURE_REPORT)
    assert report.ticket_id == "PLAT-1234"
    assert report.affected_files[0].path.endswith("handler.py")


def test_malformed_research_report_fails_loudly(tmp_path):
    bad = tmp_path / "bad_report.json"
    bad.write_text('{"ticket_id": "X"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid ResearchReport"):
        load_research_report(bad)


def test_planner_tools_read_grep_find_only(tmp_path):
    registry, names = build_planner_tools(str(tmp_path))
    try:
        assert names == get_allowed_tools("planner")
        assert "write" not in names
        assert "bash" not in names
    finally:
        registry.dispose()


def test_build_semantic_plan_prompt_requires_semantic_targets():
    report = load_research_report(FIXTURE_REPORT)
    prompt = build_semantic_plan_prompt(report, str(REPO_ROOT))
    assert "target_type and target_name" in prompt
    assert "caution_flag" in prompt


def test_build_semantic_plan_prompt_includes_research():
    report = load_research_report(FIXTURE_REPORT)
    prompt = build_semantic_plan_prompt(report, str(REPO_ROOT))
    assert "PLAT-1234" in prompt
    assert "research_output" in prompt
    assert "instruction" in prompt
    assert "acceptance checklist" in prompt.lower()


def test_run_planner_uses_mock_without_cursor(tmp_path):
    """Without CURSOR_API_KEY, run_planner returns mock semantic plan."""
    report = load_research_report(FIXTURE_REPORT)
    demo = tmp_path / "handler.py"
    demo.write_text('sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n', encoding="utf-8")
    rel = str(demo.relative_to(tmp_path))
    report.affected_files[0].path = rel

    plan = run_planner(report, str(tmp_path))
    assert plan.ticket_id == "PLAT-1234"
    assert "planner_no_cursor_api_key" in plan.flags
    edit_steps = [s for s in plan.execution_steps if s.action == "EDIT"]
    assert len(edit_steps) == 1
    assert edit_steps[0].instruction
    assert edit_steps[0].intent_marker


def test_run_planner_with_cursor_single_ask(tmp_path, monkeypatch):
    """Planner calls Cursor ask exactly once when API key is set."""
    report = load_research_report(FIXTURE_REPORT)
    rel = "handler.py"
    demo = tmp_path / rel
    demo.write_text('sql = "SELECT * FROM users WHERE name LIKE ?"\n', encoding="utf-8")
    report.affected_files[0].path = rel

    plan_json = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="SQLi",
        files_to_change=[rel],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                file=rel,
                target="read",
                reason="ground",
                validate="ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                file=rel,
                target="fix",
                reason="fix",
                validate="ok",
                target_type="assignment",
                target_name="sql",
                approximate_line=1,
                instruction="Use parameterized query",
                intent_marker="LIKE ?",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                file=rel,
                target="verify",
                reason="verify",
                validate="grep for LIKE ?",
                intent_marker="LIKE ?",
            ),
        ],
        risk_level="high",
    )

    mock_gateway = MagicMock()
    mock_gateway.ask.return_value = CursorGatewayResult(
        status="completed",
        text=json.dumps(plan_json.model_dump()),
        method="ask",
    )

    monkeypatch.setenv("CURSOR_API_KEY", "test-key")
    monkeypatch.setattr(
        "src.agents.planner.agent.get_cursor_gateway",
        lambda *_args, **_kwargs: mock_gateway,
    )

    plan = run_planner(report, str(tmp_path))
    mock_gateway.ask.assert_called_once()
    mock_gateway.search.assert_not_called()
    assert "cursor_semantic_plan" in plan.flags


def test_normalize_plan_remaps_temp_clone_paths(tmp_path):
    app = tmp_path / "sqli" / "app.py"
    app.parent.mkdir(parents=True)
    app.write_text("setup_jinja(app, autoescape=False)\n", encoding="utf-8")

    report = ResearchReport(
        ticket_id="SEC-1",
        research_summary="x",
        hypotheses=[
            {"rank": 1, "hypothesis": "h", "evidence_strength": "strong", "evidence": ["e"]}
        ],
        affected_files=[
            {
                "path": "temp_clone/sqli/app.py",
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
        files_to_change=["temp_clone/sqli/app.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                file="temp_clone/sqli/app.py",
                target="read",
                reason="r",
                validate="x",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                file="temp_clone/sqli/app.py",
                target="edit",
                reason="r",
                validate="x",
                approximate_line=1,
                instruction="Enable autoescape. Acceptance: (1) autoescape=True.",
                intent_marker="autoescape=True",
                target_type="assignment",
                target_name="autoescape",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                file="temp_clone/sqli/app.py",
                target="verify",
                reason="r",
                validate="x",
                intent_marker="autoescape=True",
            ),
        ],
        risk_level="high",
    )
    plan = _normalize_plan_paths(plan, report, str(tmp_path))
    assert plan.files_to_change == ["sqli/app.py"]
    hard, _soft = validate_instruction_edits(plan, str(tmp_path))
    assert hard == []


def test_normalize_plan_replaces_dot_where_with_affected_file():
    report = ResearchReport(
        ticket_id="SEC-1",
        research_summary="x",
        hypotheses=[
            {"rank": 1, "hypothesis": "h", "evidence_strength": "strong", "evidence": ["e"]}
        ],
        affected_files=[
            {
                "path": "src/example/handler.py",
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
                file=".",
                target="bad",
                reason="bad",
                validate="x",
            ),
        ],
        risk_level="high",
    )
    plan = _normalize_plan_paths(plan, report, "/repo")
    _validate_plan_executable(plan, report, "/repo")
    assert plan.execution_steps[0].file.endswith("handler.py")


def test_validate_plan_rejects_dot_where_without_candidates():
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
                file=".",
                target="bad",
                reason="bad",
                validate="x",
            ),
        ],
        risk_level="low",
    )
    with pytest.raises(ValueError, match="invalid file"):
        _validate_plan_executable(plan, report, "/repo")


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
    assert payload["ticket_id"] == "PLAT-1234"


def test_synthesize_micro_research_output_extracts_paths(tmp_path):
    """MICRO route synthesis must pass repo_path to file-path extraction."""
    repo = tmp_path / "repo"
    (repo / "sqli").mkdir(parents=True)
    (repo / "sqli" / "app.py").write_text("# app\n", encoding="utf-8")
    state = {
        "jira_ticket_id": "SPACE-1",
        "repo_path": str(repo),
        "jira_ticket": {
            "issue_id": "SPACE-1",
            "title": "Debug flag",
            "problem_statement": "Turn off debug in sqli/app.py",
            "module": "sqli",
        },
    }
    out = synthesize_micro_research_output(state)
    assert out["ticket_id"] == "SPACE-1"
    assert any("sqli/app.py" in f["path"] for f in out["affected_files"])
