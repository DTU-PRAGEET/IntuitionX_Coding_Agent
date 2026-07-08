"""Tests for planner instruction edit validation."""

from __future__ import annotations

from src.core.plan_validation import validate_instruction_edits
from src.core.schemas import ExecutionPlan, ExecutionStep


def test_instruction_validation_does_not_fail_on_duplicate_function_names(tmp_path):
    rel = "dup.py"
    fp = tmp_path / rel
    fp.write_text("def foo():\n    pass\n\ndef foo():\n    pass\n", encoding="utf-8")

    plan = ExecutionPlan(
        ticket_id="T",
        root_hypothesis="h",
        files_to_change=[rel],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file=rel,
                target="fix",
                reason="r",
                validate="v",
                target_type="function",
                target_name="foo",
                instruction="Fix foo. Acceptance: (1) safe.",
                intent_marker="FIXED",
            ),
        ],
        risk_level="low",
    )
    hard, _soft = validate_instruction_edits(plan, str(tmp_path))
    assert hard == []
