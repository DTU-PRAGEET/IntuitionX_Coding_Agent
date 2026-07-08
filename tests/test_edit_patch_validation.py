"""Tests for instruction EDIT validation in plan_validation."""

from __future__ import annotations

from src.core.plan_validation import validate_instruction_edits
from src.core.schemas import ExecutionPlan, ExecutionStep


def _plan(path: str = "foo.py") -> ExecutionPlan:
    return ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=[path],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                file=path,
                target="read",
                reason="ground",
                validate="ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                file=path,
                target="edit",
                reason="fix",
                validate="grep for fixed",
                approximate_line=1,
                instruction="Remove secret and use env var. Acceptance: (1) env used.",
                intent_marker="fixed",
                target_type="assignment",
                target_name="secret",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                file=path,
                target="verify",
                reason="check",
                validate="ok",
                intent_marker="fixed",
            ),
        ],
        risk_level="low",
    )


def test_instruction_validation_passes_when_file_exists(tmp_path):
    fp = tmp_path / "foo.py"
    fp.write_text("secret = 'hardcoded'\nmore = 1\n", encoding="utf-8")
    hard, _soft = validate_instruction_edits(_plan(), str(tmp_path))
    assert hard == []


def test_instruction_validation_fails_missing_file(tmp_path):
    hard, _soft = validate_instruction_edits(_plan(), str(tmp_path))
    assert any("not found" in e for e in hard)


def test_instruction_validation_fails_missing_target_name(tmp_path):
    fp = tmp_path / "foo.py"
    fp.write_text("x = 1\n", encoding="utf-8")
    plan = _plan()
    plan.execution_steps[1].target_type = "function"
    plan.execution_steps[1].target_name = ""
    hard, _soft = validate_instruction_edits(plan, str(tmp_path))
    assert any("missing target_name" in e for e in hard)
