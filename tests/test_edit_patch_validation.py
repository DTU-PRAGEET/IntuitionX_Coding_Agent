"""Tests for semantic EDIT validation."""

from __future__ import annotations

from src.core.plan_validation import validate_semantic_edits
from src.core.schemas import ExecutionPlan, ExecutionStep


def _semantic_plan(path: str = "foo.py") -> ExecutionPlan:
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
                target_type="block",
                approximate_line=1,
                end_line=2,
                instruction="Remove secret and use env var",
                intent_marker="fixed",
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


def test_semantic_validation_passes_when_target_unique(tmp_path):
    fp = tmp_path / "foo.py"
    fp.write_text("secret = 'hardcoded'\nmore = 1\n", encoding="utf-8")
    hard, _soft = validate_semantic_edits(_semantic_plan(), str(tmp_path))
    assert hard == []


def test_semantic_validation_fails_missing_file(tmp_path):
    hard, _soft = validate_semantic_edits(_semantic_plan(), str(tmp_path))
    assert any("not found" in e for e in hard)


def test_semantic_validation_fails_unresolved_target(tmp_path):
    fp = tmp_path / "foo.py"
    fp.write_text("x = 1\n", encoding="utf-8")
    plan = _semantic_plan()
    plan.execution_steps[1].target_type = "function"
    plan.execution_steps[1].target_name = "missing_fn"
    hard, _soft = validate_semantic_edits(plan, str(tmp_path))
    assert any("could not resolve" in e for e in hard)
