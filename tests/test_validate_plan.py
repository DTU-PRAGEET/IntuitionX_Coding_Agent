"""Tests for execution plan triad validation."""

from __future__ import annotations

from src.core.plan_validation import validate_plan_triad
from src.core.schemas import ExecutionPlan, ExecutionStep


def test_valid_read_edit_verify_triad():
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["foo.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                file="foo.py",
                target="read",
                reason="ground",
                validate="ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                file="foo.py",
                target="edit",
                reason="fix",
                validate="ok",
                old_string="a",
                new_string="b",
                intent_marker="b",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                file="foo.py",
                target="verify",
                reason="check",
                validate="ok",
                intent_marker="b",
            ),
        ],
        risk_level="low",
    )
    assert validate_plan_triad(plan) == []


def test_invalid_edit_without_read():
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["foo.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file="foo.py",
                target="edit",
                reason="fix",
                validate="ok",
                old_string="a",
                new_string="b",
                intent_marker="b",
            ),
        ],
        risk_level="low",
    )
    errors = validate_plan_triad(plan)
    assert any("preceding READ" in e for e in errors)
