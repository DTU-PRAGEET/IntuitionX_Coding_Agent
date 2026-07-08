"""Tests for instruction-based EDIT validation."""

from __future__ import annotations

from pathlib import Path

import pytest

from pydantic import ValidationError

from src.core.plan_validation import validate_instruction_edits
from src.core.schemas import ExecutionPlan, ExecutionStep


def _edit_step(**overrides) -> ExecutionStep:
    base = dict(
        step_id=2,
        action="EDIT",
        file="app.py",
        target="fix",
        reason="security",
        validate="grep FIXED",
        instruction="Replace secret with env var. Acceptance: (1) env used, (2) no hardcoded secret.",
        intent_marker="FIXED",
        target_type="assignment",
        target_name="secret",
    )
    base.update(overrides)
    return ExecutionStep(**base)


def test_validate_instruction_edits_passes_valid_plan(tmp_path):
    (tmp_path / "app.py").write_text("secret = 'bad'\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["app.py"],
        execution_steps=[_edit_step()],
        risk_level="low",
    )
    hard, soft = validate_instruction_edits(plan, str(tmp_path))
    assert hard == []
    assert soft == []


def test_validate_instruction_edits_missing_instruction(tmp_path):
    (tmp_path / "app.py").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="instruction"):
        _edit_step(instruction="")


def test_validate_instruction_edits_missing_file(tmp_path):
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["missing.py"],
        execution_steps=[_edit_step(file="missing.py")],
        risk_level="low",
    )
    hard, _soft = validate_instruction_edits(plan, str(tmp_path))
    assert any("not found" in e for e in hard)


def test_validate_instruction_edits_missing_target_type(tmp_path):
    (tmp_path / "app.py").write_text("x\n", encoding="utf-8")
    with pytest.raises(ValidationError, match="target_type"):
        _edit_step(target_type=None)


def test_validate_instruction_edits_passes_with_caution_flag(tmp_path):
    (tmp_path / "app.py").write_text("secret = 'bad'\nsecret = 'also'\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["app.py"],
        execution_steps=[
            _edit_step(
                caution_flag="CAUTION: 'secret' appears twice; fix the assignment in main flow only.",
            )
        ],
        risk_level="low",
    )
    hard, soft = validate_instruction_edits(plan, str(tmp_path))
    assert hard == []
    assert soft == []


def test_validate_instruction_edits_rejects_legacy_patch(tmp_path):
    (tmp_path / "app.py").write_text("a\n", encoding="utf-8")
    with pytest.raises(ValueError, match="legacy patch"):
        _edit_step(old_string="a", new_string="b")
