"""Tests for Executor Agent (Level 4b)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from langchain_core.messages import AIMessage

from src.agents.executor.agent import ExecutionResult, load_execution_plan, run_executor
from src.agents.executor.tools.builder import build_executor_tools
from src.core.tool_permissions import get_allowed_tools
from src.core.schemas import ExecutionPlan, ExecutionStep

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PLAN = REPO_ROOT / "src/agents/executor/fixtures/mock_plan.json"


def test_load_mock_plan():
    plan = load_execution_plan(FIXTURE_PLAN)
    assert plan.ticket_id == "EXEC-DEMO-1"
    assert plan.create_files is True
    assert len(plan.execution_steps) >= 2


def test_malformed_plan_fails_loudly(tmp_path):
    bad = tmp_path / "bad_plan.json"
    bad.write_text('{"ticket_id": "X"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid ExecutionPlan"):
        load_execution_plan(bad)


def test_executor_tools_write_edit_bash_only(tmp_path):
    registry, names = build_executor_tools(str(tmp_path))
    try:
        assert set(names) == set(get_allowed_tools("executor"))
        assert "read" not in names
        assert "grep" not in names
        assert "git_worktree" in names
        assert "mr_create" in names
    finally:
        registry.dispose()


def test_run_executor_write_and_edit(tmp_path):
    plan = ExecutionPlan(
        ticket_id="TEST-1",
        root_hypothesis="write and edit demo",
        files_to_change=["hello.txt"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="hello.txt",
                what="Create file",
                why="Setup",
                validate="exists",
                new_string="Hello, world!\n",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                where="hello.txt",
                what="Update greeting",
                why="Patch",
                validate="updated",
                old_string="Hello, world!\n",
                new_string="Hello, Executor!\n",
            ),
        ],
        create_files=True,
        risk_level="low",
    )
    result = run_executor(plan, str(tmp_path))
    assert isinstance(result, ExecutionResult)
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "Hello, Executor!\n"
    assert result.steps_completed == 2


def test_run_executor_edit_failure(tmp_path):
    (tmp_path / "hello.txt").write_text("unchanged\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="TEST-2",
        root_hypothesis="bad patch",
        files_to_change=["hello.txt"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="hello.txt",
                what="bad patch",
                why="fail demo",
                validate="n/a",
                old_string="NOT_IN_FILE",
                new_string="replacement\n",
            ),
        ],
        risk_level="low",
    )
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content='{"old_string": "still wrong", "new_string": "x"}')

    with pytest.raises(RuntimeError, match="step 1"):
        run_executor(plan, str(tmp_path), llm=mock_llm)


def test_verify_ignores_planner_validate_fragments_when_edit_patterns_exist(tmp_path):
    """Bad planner validate prose must not override prior EDIT new_string checks."""
    from src.agents.executor.agent import _execute_verify
    from src.agents.executor.tools.builder import build_executor_tools

    target = tmp_path / "UserSearchController.py"
    target.write_text(
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        '    cursor.execute(sql, (f"%{query}%",))\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi fix",
        files_to_change=["UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="UserSearchController.py",
                what="Parameterize SQL",
                why="fix",
                validate="patched",
                old_string='sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n    cursor.execute(sql)',
                new_string=(
                    '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
                    '    cursor.execute(sql, (f"%{query}%",))'
                ),
            ),
            ExecutionStep(
                step_id=2,
                action="VERIFY",
                where="UserSearchController.py",
                what="Confirm fix",
                why="Ensure fix applied",
                validate=(
                    'cursor.execute("SELECT * FROM users WHERE name LIKE ?",; '
                    'f"%{query}%");; '
                    '!f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"'
                ),
            ),
        ],
        risk_level="high",
    )
    registry, _ = build_executor_tools(str(tmp_path))
    try:
        verify_step = plan.execution_steps[1].model_dump(by_alias=True)
        ok, _out = _execute_verify(registry, verify_step, plan)
    finally:
        registry.dispose()
    assert ok


def test_verify_requires_all_validate_patterns(tmp_path):
    """VERIFY must match every pattern in validate — not stop after the first hit."""
    target = tmp_path / "UserSearchController.py"
    target.write_text(
        'sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        'params = (f"%{query}%",)\n'
        'cursor.execute(sql)\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi fix",
        files_to_change=["UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="VERIFY",
                where="UserSearchController.py",
                what="Confirm full parameterized query fix",
                why="Ensure parameters are passed to execute",
                validate=(
                    'grep for sql = "SELECT * FROM users WHERE name LIKE ?"; '
                    "grep for cursor.execute(sql, params)"
                ),
            ),
        ],
        risk_level="high",
    )
    with pytest.raises(RuntimeError, match="step 1"):
        run_executor(plan, str(tmp_path))


def test_verify_passes_when_full_fix_applied(tmp_path):
    target = tmp_path / "UserSearchController.py"
    target.write_text(
        'sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        'params = (f"%{query}%",)\n'
        'cursor.execute(sql, params)\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi fix",
        files_to_change=["UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="VERIFY",
                where="UserSearchController.py",
                what="Confirm parameterized query and execute call",
                why="Ensure fix applied",
                validate=(
                    'grep for sql = "SELECT * FROM users WHERE name LIKE ?"; '
                    "grep for cursor.execute(sql, params)"
                ),
            ),
        ],
        risk_level="high",
    )
    result = run_executor(plan, str(tmp_path))
    assert result.steps_completed == 1
    assert "cursor.execute(sql, params)" in target.read_text(encoding="utf-8")


def test_verify_checks_prior_edit_new_and_old_strings(tmp_path):
    """VERIFY auto-checks latest EDIT new_string lines and rejects remaining old_string lines."""
    from src.agents.executor.agent import _execute_verify
    from src.agents.executor.tools.builder import build_executor_tools

    target = tmp_path / "UserSearchController.py"
    target.write_text(
        'sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        'params = (f"%{query}%",)\n'
        'cursor.execute(sql)\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi fix",
        files_to_change=["UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="UserSearchController.py",
                what="Parameterize SQL",
                why="fix",
                validate="patched",
                old_string='sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\ncursor.execute(sql)',
                new_string=(
                    'sql = "SELECT * FROM users WHERE name LIKE ?"\n'
                    'params = (f"%{query}%",)\n'
                    'cursor.execute(sql, params)'
                ),
            ),
            ExecutionStep(
                step_id=2,
                action="VERIFY",
                where="UserSearchController.py",
                what="Confirm fix complete",
                why="Ensure execute uses params",
                validate="grep for parameterized query",
            ),
        ],
        risk_level="high",
    )
    registry, _ = build_executor_tools(str(tmp_path))
    try:
        verify_step = plan.execution_steps[1].model_dump(by_alias=True)
        ok, out = _execute_verify(registry, verify_step, plan)
    finally:
        registry.dispose()
    assert not ok
    assert "cursor.execute(sql, params)" in out or "required pattern" in out


def test_edit_applies_full_planned_patch(tmp_path):
    target = tmp_path / "UserSearchController.py"
    target.write_text(
        'sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
        'cursor.execute(sql)\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi fix",
        files_to_change=["UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="UserSearchController.py",
                what="Parameterize SQL",
                why="fix",
                validate="patched",
                old_string=(
                    'sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
                    'cursor.execute(sql)'
                ),
                new_string=(
                    'sql = "SELECT * FROM users WHERE name LIKE ?"\n'
                    'params = (f"%{query}%",)\n'
                    'cursor.execute(sql, params)'
                ),
            ),
        ],
        risk_level="high",
    )
    result = run_executor(plan, str(tmp_path))
    content = target.read_text(encoding="utf-8")
    assert result.steps_completed == 1
    assert 'sql = f"SELECT' not in content
    assert "cursor.execute(sql, params)" in content
    assert "cursor.execute(sql)\n" not in content


def test_fallback_noop_accepted_when_intent_already_satisfied(tmp_path):
    """When fallback edit is a no-op but file is already fixed, accept by intent."""
    target = tmp_path / "UserSearchController.py"
    target.write_text(
        'def search_users(query: str) -> list[dict[str, Any]]:\n'
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        '    params = (f"%{query}%",)\n'
        '    return [{"sql": sql, "params": params, "query": query}]\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi fix",
        files_to_change=["UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="UserSearchController.py",
                what="Parameterize SQL",
                why="fix",
                validate='grep for LIKE ?; !f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"',
                old_string="truncated-prefix-that-wont-match",
                new_string='sql = "SELECT * FROM users WHERE name LIKE ?"\nparams = (f"%{query}%",)',
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
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(
        content=json.dumps(
            {
                "old_string": 'def search_users(query: str) -> list[dict[str, Any]]:\n    sql = "SELECT * FROM users WHERE name LIKE ?"',
                "new_string": 'def search_users(query: str) -> list[dict[str, Any]]:\n    sql = "SELECT * FROM users WHERE name LIKE ?"',
            }
        )
    )
    result = run_executor(plan, str(tmp_path), llm=mock_llm)
    assert result.steps_completed == 2

    """LLM fallback that only changes the SQL string must fail completeness check."""
    target = tmp_path / "UserSearchController.py"
    target.write_text(
        'sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
        'cursor.execute(sql)\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SEC-4421",
        root_hypothesis="SQLi fix",
        files_to_change=["UserSearchController.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="UserSearchController.py",
                what="Parameterize SQL",
                why="fix",
                validate="patched",
                old_string="NOT_IN_FILE",
                new_string=(
                    'sql = "SELECT * FROM users WHERE name LIKE ?"\n'
                    'params = (f"%{query}%",)\n'
                    'cursor.execute(sql, params)'
                ),
                change="Replace f-string SQL with parameterized query and pass params to execute",
            ),
        ],
        risk_level="high",
    )
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = [
        AIMessage(
            content=json.dumps(
                {
                    "old_string": 'sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"',
                    "new_string": 'sql = "SELECT * FROM users WHERE name LIKE ?"\nparams = (f"%{query}%",)',
                }
            )
        ),
        AIMessage(
            content=json.dumps(
                {
                    "old_string": 'sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"',
                    "new_string": 'sql = "SELECT * FROM users WHERE name LIKE ?"\nparams = (f"%{query}%",)',
                }
            )
        ),
    ]

    with pytest.raises(RuntimeError, match="incomplete"):
        run_executor(plan, str(tmp_path), llm=mock_llm)
    assert "cursor.execute(sql)" in target.read_text(encoding="utf-8")
    assert "cursor.execute(sql, params)" not in target.read_text(encoding="utf-8")


def test_run_executor_mock_fixture_end_to_end(tmp_path):
    plan = load_execution_plan(FIXTURE_PLAN)
    result = run_executor(plan, str(tmp_path))
    assert (tmp_path / "hello.txt").read_text(encoding="utf-8") == "Hello, Executor!\n"
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == 'VERSION = "1.0.0"\n'
    assert result.steps_completed == 4


def test_executor_cli_runs_fixture(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.agents.executor",
            "--plan",
            str(FIXTURE_PLAN),
            "--repo-path",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["steps_completed"] == 4
    assert (tmp_path / "hello.txt").exists()
    assert "[executor]" in result.stderr
    assert "step=1" in result.stderr or "step=1 " in result.stderr


def test_executor_dry_run_cli():
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.agents.executor",
            "--plan",
            str(FIXTURE_PLAN),
            "--repo-path",
            "/tmp",
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["dry_run"] is True
    assert payload["ticket_id"] == "EXEC-DEMO-1"
