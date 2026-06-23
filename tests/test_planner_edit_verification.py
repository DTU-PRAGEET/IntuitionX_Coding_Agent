"""Tests for planner EDIT verification (seen_reads fix)."""

from __future__ import annotations

from unittest.mock import MagicMock

from src.agents.planner.agent import _verify_plan_with_tools
from src.core.schemas import ExecutionPlan, ExecutionStep
from src.tools.registry import ToolRegistry


def test_edit_verification_runs_after_read_on_same_file(tmp_path):
    target = tmp_path / "student.py"
    target.write_text(
        "class Student:\n    pass\n",
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["student.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where="student.py",
                what="read",
                why="ground",
                validate="ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                where="student.py",
                what="edit",
                why="fix",
                validate="ok",
                old_string="NOT_IN_FILE",
                new_string="class Student:\n    fixed = True",
                intent_marker="fixed",
            ),
        ],
        risk_level="low",
    )
    registry = ToolRegistry(str(tmp_path))
    try:
        flags, calls = _verify_plan_with_tools(plan, registry, max_calls=3)
    finally:
        registry.dispose()
    assert calls >= 1
    assert any("old_string not found" in f for f in flags)


def test_edit_verification_passes_when_patch_unique(tmp_path):
    target = tmp_path / "student.py"
    target.write_text(
        "class Student:\n    pass\n",
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["student.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where="student.py",
                what="read",
                why="ground",
                validate="ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                where="student.py",
                what="edit",
                why="fix",
                validate="ok",
                old_string="class Student:\n    pass",
                new_string="class Student:\n    fixed = True",
                intent_marker="fixed",
            ),
        ],
        risk_level="low",
    )
    registry = MagicMock()
    registry.cwd = str(tmp_path)
    registry.execute.return_value = MagicMock(is_error=False, output="ok")
    flags, _calls = _verify_plan_with_tools(plan, registry, max_calls=3)
    edit_flags = [f for f in flags if "EDIT step" in f]
    assert edit_flags == []
