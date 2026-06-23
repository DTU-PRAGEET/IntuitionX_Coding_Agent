"""Tests for executor fallback intent validation."""

from __future__ import annotations

from src.agents.executor.agent import _extract_grep_patterns_from_validate, _validate_edit_intent
from src.agents.executor.tools.builder import build_executor_tools
from src.core.schemas import ExecutionPlan, ExecutionStep


def test_validate_edit_intent_accepts_parameterized_sql(tmp_path):
    target = tmp_path / "UserSearchController.py"
    target.write_text(
        'def search_users(query: str):\n'
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        '    cursor.execute(sql, (f"%{query}%",))\n'
        '    return cursor.fetchall()\n',
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
                old_string="ignored",
                new_string='cursor.execute(sql, (f"%{query}%",))',
            ),
            ExecutionStep(
                step_id=2,
                action="VERIFY",
                where="UserSearchController.py",
                what="verify",
                why="verify",
                validate='grep for LIKE ?; !f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"',
            ),
        ],
        risk_level="high",
    )
    registry, _ = build_executor_tools(str(tmp_path))
    try:
        step = plan.execution_steps[0].model_dump(by_alias=True)
        ok, msg = _validate_edit_intent(registry, step, plan)
    finally:
        registry.dispose()
    assert ok, msg


def test_extract_grep_patterns_strips_backticks():
    required, forbidden = _extract_grep_patterns_from_validate(
        'grep for `sql = "SELECT * FROM users WHERE name LIKE ?"`; '
        "!`LIKE '%{query}%'`"
    )
    assert 'LIKE ?' in required[0]
    # Forbidden backtick tokens without "grep for" prefix are handled by core SQLi heuristics.
