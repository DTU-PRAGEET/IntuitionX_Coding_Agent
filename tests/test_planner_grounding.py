"""Tests for planner patch grounding from disk."""

from __future__ import annotations

from src.agents.planner.agent import _ground_edit_patches_from_disk
from src.core.schemas import ExecutionPlan, ExecutionStep


def test_ground_edit_patches_replaces_hallucinated_old_string(tmp_path):
    target = tmp_path / "UserSearchController.py"
    target.write_text(
        'def search_users(query: str) -> list[dict[str, Any]]:\n'
        '    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
        '    return [{"sql": sql}]\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="fix",
        files_to_change=["UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="UserSearchController.py",
                what="fix",
                why="fix",
                validate="patched",
                old_string='def search_users(query: str):\n    cursor.execute(sql)\n',
                new_string='sql = "SELECT * FROM users WHERE name LIKE ?"\n',
            )
        ],
        risk_level="high",
    )
    grounded = _ground_edit_patches_from_disk(plan, str(tmp_path))
    edit = grounded.execution_steps[0]
    assert 'f"SELECT' in (edit.old_string or "")
    assert "cursor.execute" not in (edit.old_string or "")
    assert 'LIKE ?' in (edit.new_string or "")


def test_ground_truncated_patch_when_prefix_in_file(tmp_path):
    target = tmp_path / "UserSearchController.py"
    target.write_text(
        '"""Canonical vulnerable demo file for --mock-security pipeline resets."""\n\n'
        "from typing import Any\n\n\n"
        'def search_users(query: str) -> list[dict[str, Any]]:\n'
        '    """Search users — intentionally vulnerable SQL concatenation for demo."""\n'
        '    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
        '    return [{"sql": sql, "query": query}]\n',
        encoding="utf-8",
    )
    prefix_old = (
        '"""Canonical vulnerable demo file for --mock-security pipeline resets."""\n\n'
        "from typing import Any\n\n\n"
        'def search_users(query: str) -> list[dict[str, Any]]:\n'
        '    """Search users — intentionally vulnerable'
    )
    plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="fix",
        files_to_change=["UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="UserSearchController.py",
                what="fix",
                why="fix",
                validate="patched",
                old_string=prefix_old,
                new_string='"""Search users — safe parameterized SQL p',
            )
        ],
        risk_level="high",
    )
    grounded = _ground_edit_patches_from_disk(plan, str(tmp_path))
    edit = grounded.execution_steps[0]
    assert 'f"SELECT' in (edit.old_string or "")
    assert 'params = (f"%{query}%",)' in (edit.new_string or "")


def test_ground_aligns_trailing_newline_mismatch(tmp_path):
    target = tmp_path / "sqli/dao/student.py"
    target.parent.mkdir(parents=True)
    content = "vulnerable\n\n\n"
    target.write_text(content, encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="SPACE-129068",
        root_hypothesis="fix",
        files_to_change=["sqli/dao/student.py"],
        execution_steps=[
            ExecutionStep(
                step_id=3,
                action="EDIT",
                where="sqli/dao/student.py",
                what="fix",
                why="fix",
                validate="patched",
                old_string=content + "\n",
                new_string="fixed\n\n\n",
                intent_marker="fixed",
            )
        ],
        risk_level="high",
    )
    grounded = _ground_edit_patches_from_disk(plan, str(tmp_path))
    edit = grounded.execution_steps[0]
    assert edit.old_string == content
