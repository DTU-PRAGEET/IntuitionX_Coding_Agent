"""Tests for executor pre-edit validation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agents.executor.agent import _execute_edit, run_executor
from src.agents.executor.tools.builder import build_executor_tools
from src.core.schemas import ExecutionPlan, ExecutionStep


def test_executor_rejects_identical_patch_without_edit_tool(tmp_path):
    (tmp_path / "f.py").write_text("unchanged\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="bad",
        files_to_change=["f.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="f.py",
                what="noop",
                why="bad plan",
                validate="n/a",
                old_string="unchanged",
                new_string="unchanged",
            ),
        ],
        risk_level="low",
    )
    registry, _ = build_executor_tools(str(tmp_path))
    try:
        ok, out, _devs = _execute_edit(
            registry,
            plan.execution_steps[0].model_dump(),
            plan,
            create_files=False,
            llm=MagicMock(),
        )
    finally:
        registry.dispose()
    assert ok is False
    assert "identical" in out.lower()


def test_executor_snapshot_mismatch_raises(tmp_path):
    (tmp_path / "f.py").write_text("x\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="snap",
        files_to_change=["f.py"],
        planner_snapshot_head="deadbeef" * 5,
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where="f.py",
                what="read",
                why="r",
                validate="ok",
            ),
        ],
        risk_level="low",
    )
    with pytest.raises(RuntimeError, match="snapshot mismatch"):
        run_executor(plan, str(tmp_path))
