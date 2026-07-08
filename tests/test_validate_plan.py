"""Tests for execution plan triad validation."""

from __future__ import annotations

from src.core.plan_validation import sync_files_to_change_from_edit_steps, validate_plan_triad
from src.core.schemas import ExecutionPlan, ExecutionStep


def test_valid_read_edit_verify_triad():
    """Test Valid Read Edit Verify Triad for this module."""
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
                instruction="replace a with b. Acceptance: (1) b present.",
                intent_marker="b",
                target_type="assignment",
                target_name="a",
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
    """Test Invalid Edit Without Read for this module."""
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
                instruction="replace a with b. Acceptance: (1) b present.",
                intent_marker="b",
                target_type="assignment",
                target_name="a",
            ),
        ],
        risk_level="low",
    )
    errors = validate_plan_triad(plan)
    assert any("preceding READ" in e for e in errors)


def test_sync_files_to_change_from_edit_steps_adds_missing_paths():
    """Planner LLM may omit EDIT targets from files_to_change — repair before executor."""
    plan = ExecutionPlan(
        ticket_id="SPACE-129070",
        root_hypothesis="remove hardcoded secrets",
        files_to_change=[
            "src/main/resources/application.properties",
            "src/main/java/org/owasp/wrongsecrets/challenges/docker/WrongSecretsConstants.java",
        ],
        execution_steps=[
            ExecutionStep(
                step_id=7,
                action="READ",
                file=".ssh/wrongsecrets.keys",
                target="read ssh key file",
                reason="ground",
                validate="ok",
            ),
            ExecutionStep(
                step_id=8,
                action="EDIT",
                file=".ssh/wrongsecrets.keys",
                target="redact ssh key",
                reason="remove private key",
                validate="grep REMOVED_FOR_SECURITY",
                target_type="file",
                instruction="Replace private key with security placeholder comments",
                intent_marker="REMOVED_FOR_SECURITY",
            ),
            ExecutionStep(
                step_id=9,
                action="VERIFY",
                file=".ssh/wrongsecrets.keys",
                target="verify redaction",
                reason="confirm",
                validate="grep REMOVED_FOR_SECURITY",
                intent_marker="REMOVED_FOR_SECURITY",
            ),
        ],
        risk_level="high",
    )
    synced = sync_files_to_change_from_edit_steps(plan)
    assert ".ssh/wrongsecrets.keys" in synced.files_to_change
    assert "files_to_change_synced_from_edit_steps" in synced.flags
