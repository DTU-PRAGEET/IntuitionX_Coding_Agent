"""Tests for Cursor implement runner."""

from __future__ import annotations

from src.core.schemas import FixPlan, FixTarget, PatchResult
from src.integrations.cursor.implement_runner import (
    build_edit_instruction,
    mock_patch_result,
    validate_patch,
)


def _sample_fix_plan() -> FixPlan:
    """Sample Fix Plan for this module."""
    return FixPlan(
        ticket_id="SEC-IMP1",
        root_cause="SQL injection in user search",
        primary_fix_type="parameterize_sql",
        targets=[
            FixTarget(
                file_path="src/demo/UserDao.java",
                start_line=40,
                end_line=55,
                fix_type="parameterize_sql",
                rationale="Replace string concat with PreparedStatement",
            )
        ],
        files_to_change=["src/demo/UserDao.java"],
        recommendations=["Use parameterized queries"],
    )


def test_build_edit_instruction_includes_targets():
    """Test Build Edit Instruction Includes Targets for this module."""
    plan = _sample_fix_plan()
    text = build_edit_instruction(plan)
    assert "SEC-IMP1" in text
    assert "UserDao.java" in text
    assert "parameterize_sql" in text


def test_mock_patch_result_dry_run():
    """Test Mock Patch Result Dry Run for this module."""
    plan = _sample_fix_plan()
    patch = mock_patch_result(plan, worktree_path="/tmp/wt")
    assert patch.status == "dry_run"
    assert patch.changed_files == plan.files_to_change
    assert patch.worktree_path == "/tmp/wt"


def test_validate_patch_success():
    """Test Validate Patch Success for this module."""
    plan = _sample_fix_plan()
    patch = validate_patch(
        plan,
        cursor_status="finished",
        changed_files=["src/demo/UserDao.java"],
        git_diff_stat=" 1 file changed",
        dry_run=False,
        exit_code=0,
    )
    assert patch.status == "success"
    assert isinstance(patch, PatchResult)


def test_validate_patch_failure_no_diff():
    """Test Validate Patch Failure No Diff for this module."""
    plan = _sample_fix_plan()
    patch = validate_patch(
        plan,
        cursor_status="error",
        changed_files=[],
        git_diff_stat="(no diff)",
        dry_run=False,
        exit_code=1,
    )
    assert patch.status == "failed"
    assert patch.errors
