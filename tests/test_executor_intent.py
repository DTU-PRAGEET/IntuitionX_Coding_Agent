"""Tests for executor fallback intent validation."""

from __future__ import annotations

from src.agents.executor.agent import _extract_grep_patterns_from_validate, _edit_targets_sql, _validate_edit_intent
from src.agents.executor.tools.builder import build_executor_tools
from src.core.schemas import ExecutionPlan, ExecutionStep


def test_validate_edit_intent_accepts_parameterized_sql(tmp_path):
    """Test Validate Edit Intent Accepts Parameterized Sql for this module."""
    target = tmp_path / "handler.py"
    target.write_text(
        'def search_users(query: str):\n'
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        '    cursor.execute(sql, (f"%{query}%",))\n'
        '    return cursor.fetchall()\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="fix",
        files_to_change=["handler.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file="handler.py",
                target="fix",
                reason="fix",
                validate="patched",
                target_type="assignment",
                target_name="sql",
                instruction="Use parameterized query with LIKE ?",
                intent_marker="LIKE ?",
            ),
            ExecutionStep(
                step_id=2,
                action="VERIFY",
                file="handler.py",
                target="verify",
                reason="verify",
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
    """Test Extract Grep Patterns Strips Backticks for this module."""
    required, forbidden = _extract_grep_patterns_from_validate(
        'grep for `sql = "SELECT * FROM users WHERE name LIKE ?"`; '
        "!`LIKE '%{query}%'`"
    )
    assert 'LIKE ?' in required[0]
    # Forbidden backtick tokens without "grep for" prefix are handled by core SQLi heuristics.


def test_validate_edit_intent_skips_sql_heuristics_for_non_sql_edits(tmp_path):
    """Test Validate Edit Intent Skips Sql Heuristics For Non Sql Edits for this module."""
    target = tmp_path / "sqli" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "setup_jinja(app, autoescape=True)  # intent:SPACE-129069-enable-autoescape\n",
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SPACE-129069",
        root_hypothesis="autoescape",
        files_to_change=["sqli/app.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file="sqli/app.py",
                target="autoescape",
                reason="XSS fix",
                validate="autoescape=True",
                target_type="block",
                target_name="setup_jinja",
                approximate_line=1,
                end_line=1,
                instruction="Enable autoescape in setup_jinja",
                intent_marker="intent:SPACE-129069-enable-autoescape",
            ),
        ],
        risk_level="medium",
    )
    registry, _ = build_executor_tools(str(tmp_path))
    try:
        step = plan.execution_steps[0].model_dump(by_alias=True)
        assert _edit_targets_sql(step) is False
        ok, msg = _validate_edit_intent(registry, step, plan)
    finally:
        registry.dispose()
    assert ok, msg
