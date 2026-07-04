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
from src.integrations.cursor.gateway import LocalPatchCursorGateway

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PLAN = REPO_ROOT / "src/agents/executor/fixtures/mock_plan.json"


def _mock_llm_pass(**extra) -> MagicMock:
    llm = MagicMock()
    payload = {
        "pass": True,
        "confidence": 0.98,
        "reason": "Fix applied",
        "missing_requirements": [],
        **extra,
    }
    llm.invoke.return_value = AIMessage(content=json.dumps(payload))
    return llm


def _validation_stub_env() -> dict[str, str]:
    return {
        "EXECUTOR_VALIDATION_STUB_RESPONSE": json.dumps(
            {
                "pass": True,
                "confidence": 0.98,
                "reason": "Fix applied",
                "missing_requirements": [],
            }
        ),
    }


def _semantic_edit(
    step_id: int,
    file: str,
    instruction: str,
    intent_marker: str,
    *,
    target_type: str = "block",
    target_name: str = "",
    approximate_line: int = 1,
    end_line: int = 5,
    validate: str | None = None,
) -> ExecutionStep:
    return ExecutionStep(
        step_id=step_id,
        action="EDIT",
        file=file,
        target=instruction[:40],
        reason="security fix",
        validate=validate or f"grep for {intent_marker}",
        target_type=target_type,  # type: ignore[arg-type]
        target_name=target_name,
        approximate_line=approximate_line,
        end_line=end_line,
        instruction=instruction,
        intent_marker=intent_marker,
    )


def _test_gateway(tmp_path: Path) -> LocalPatchCursorGateway:
    """Test Gateway for this module."""
    return LocalPatchCursorGateway(tmp_path)


def test_load_mock_plan():
    """Test Load Mock Plan for this module."""
    plan = load_execution_plan(FIXTURE_PLAN)
    assert plan.ticket_id == "EXEC-DEMO-1"
    assert plan.create_files is True
    assert len(plan.execution_steps) >= 2


def test_malformed_plan_fails_loudly(tmp_path):
    """Test Malformed Plan Fails Loudly for this module."""
    bad = tmp_path / "bad_plan.json"
    bad.write_text('{"ticket_id": "X"}', encoding="utf-8")
    with pytest.raises(ValueError, match="Invalid ExecutionPlan"):
        load_execution_plan(bad)


def test_executor_tools_read_bash_only(tmp_path):
    """Test Executor Tools Read Bash Only for this module."""
    registry, names = build_executor_tools(str(tmp_path))
    try:
        assert set(names) == set(get_allowed_tools("executor"))
        assert "read" in names
        assert "write" not in names
        assert "edit" not in names
        assert "grep" not in names
        assert "git_worktree" in names
        assert "mr_create" in names
        denied = registry.execute("edit", path="x", old_string="a", new_string="b")
        assert denied.is_error
    finally:
        registry.dispose()


def test_run_executor_semantic_edit(tmp_path):
    """Semantic edit injects intent marker via local test gateway."""
    (tmp_path / "hello.txt").write_text("Hello, world!\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="TEST-1",
        root_hypothesis="semantic edit demo",
        files_to_change=["hello.txt"],
        execution_steps=[
            _semantic_edit(
                1,
                "hello.txt",
                "Update greeting to Hello, Executor!",
                "Hello, Executor!",
                approximate_line=1,
                end_line=1,
            ),
        ],
        risk_level="low",
    )
    result = run_executor(
        plan, str(tmp_path), gateway=_test_gateway(tmp_path), llm=_mock_llm_pass()
    )
    assert isinstance(result, ExecutionResult)
    assert "Hello, Executor!" in (tmp_path / "hello.txt").read_text(encoding="utf-8")
    assert result.steps_completed == 1


def test_run_executor_edit_failure(tmp_path):
    """Failed Cursor edit raises when intent marker never appears."""
    from src.integrations.cursor.schemas import CursorEditResult

    class FailGateway(LocalPatchCursorGateway):
        def edit(self, *args, **kwargs):
            return CursorEditResult(
                status="failed",
                text="edit failed",
                stderr="edit failed",
                exit_code=1,
                changed_files=[],
            )

    (tmp_path / "hello.txt").write_text("unchanged\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="TEST-2",
        root_hypothesis="bad edit",
        files_to_change=["hello.txt"],
        execution_steps=[
            _semantic_edit(1, "hello.txt", "Apply fix", "MISSING_MARKER"),
        ],
        risk_level="low",
    )
    with pytest.raises(RuntimeError, match="step 1"):
        run_executor(plan, str(tmp_path), gateway=FailGateway(tmp_path))


def test_verify_uses_prior_edit_intent_marker(tmp_path):
    """VERIFY uses intent marker from prior semantic EDIT."""
    from src.agents.executor.agent import _execute_verify
    from src.agents.executor.tools.builder import build_executor_tools

    target = tmp_path / "handler.py"
    target.write_text(
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        '    cursor.execute(sql, (f"%{query}%",))\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="SQLi fix",
        files_to_change=["handler.py"],
        execution_steps=[
            _semantic_edit(
                1,
                "handler.py",
                "Parameterize SQL query",
                "LIKE ?",
                target_type="assignment",
                target_name="sql",
            ),
            ExecutionStep(
                step_id=2,
                action="VERIFY",
                file="handler.py",
                target="Confirm fix",
                reason="Ensure fix applied",
                validate="grep for LIKE ?",
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
    target = tmp_path / "handler.py"
    target.write_text(
        'sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        'params = (f"%{query}%",)\n'
        'cursor.execute(sql)\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="SQLi fix",
        files_to_change=["handler.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="VERIFY",
                file="handler.py",
                target="Confirm full parameterized query fix",
                reason="Ensure parameters are passed to execute",
                validate=(
                    'grep for sql = "SELECT * FROM users WHERE name LIKE ?"; '
                    "grep for cursor.execute(sql, params)"
                ),
            ),
        ],
        risk_level="high",
    )
    with pytest.raises(RuntimeError, match="step 1"):
        run_executor(plan, str(tmp_path), gateway=_test_gateway(tmp_path))


def test_verify_passes_when_full_fix_applied(tmp_path):
    """Test Verify Passes When Full Fix Applied for this module."""
    target = tmp_path / "handler.py"
    target.write_text(
        'sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        'params = (f"%{query}%",)\n'
        'cursor.execute(sql, params)\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="SQLi fix",
        files_to_change=["handler.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="VERIFY",
                file="handler.py",
                target="Confirm parameterized query and execute call",
                reason="Ensure fix applied",
                validate=(
                    'grep for sql = "SELECT * FROM users WHERE name LIKE ?"; '
                    "grep for cursor.execute(sql, params)"
                ),
            ),
        ],
        risk_level="high",
    )
    result = run_executor(
        plan, str(tmp_path), gateway=_test_gateway(tmp_path), llm=_mock_llm_pass()
    )
    assert result.steps_completed == 1
    assert "cursor.execute(sql, params)" in target.read_text(encoding="utf-8")


def test_verify_checks_prior_edit_intent_when_execute_incomplete(tmp_path):
    """VERIFY fails when prior EDIT intent marker present but execute call incomplete."""
    from src.agents.executor.agent import _execute_verify
    from src.agents.executor.tools.builder import build_executor_tools

    target = tmp_path / "handler.py"
    target.write_text(
        'sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        'params = (f"%{query}%",)\n'
        'cursor.execute(sql)\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="SQLi fix",
        files_to_change=["handler.py"],
        execution_steps=[
            _semantic_edit(
                1,
                "handler.py",
                "Pass params to execute",
                "cursor.execute(sql, params)",
                target_type="block",
                approximate_line=1,
                end_line=3,
            ),
            ExecutionStep(
                step_id=2,
                action="VERIFY",
                file="handler.py",
                target="Confirm fix complete",
                reason="Ensure execute uses params",
                validate="grep for cursor.execute(sql, params)",
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
    assert "intent_marker" in out or "required pattern" in out


def test_semantic_edit_injects_intent_marker(tmp_path):
    """Local semantic gateway injects intent marker into target file."""
    target = tmp_path / "handler.py"
    target.write_text(
        'sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
        'cursor.execute(sql)\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="SQLi fix",
        files_to_change=["handler.py"],
        execution_steps=[
            _semantic_edit(
                1,
                "handler.py",
                "Parameterize SQL and pass params to execute",
                "cursor.execute(sql, params)",
            ),
        ],
        risk_level="high",
    )
    result = run_executor(
        plan, str(tmp_path), gateway=_test_gateway(tmp_path), llm=_mock_llm_pass()
    )
    content = target.read_text(encoding="utf-8")
    assert result.steps_completed == 1
    assert "cursor.execute(sql, params)" in content


def test_edit_accepted_when_intent_already_satisfied(tmp_path):
    """When file already satisfies intent marker, edit step succeeds without mutation."""
    target = tmp_path / "handler.py"
    target.write_text(
        'def search_users(query: str) -> list[dict[str, Any]]:\n'
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        '    params = (f"%{query}%",)\n'
        '    return [{"sql": sql, "params": params, "query": query}]\n',
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="SQLi fix",
        files_to_change=["handler.py"],
        execution_steps=[
            _semantic_edit(1, "handler.py", "Ensure parameterized query", "LIKE ?"),
            ExecutionStep(
                step_id=2,
                action="VERIFY",
                file="handler.py",
                target="verify",
                reason="verify",
                validate="grep for LIKE ?",
                intent_marker="LIKE ?",
            ),
        ],
        risk_level="high",
    )
    result = run_executor(
        plan, str(tmp_path), gateway=_test_gateway(tmp_path), llm=_mock_llm_pass()
    )
    assert result.steps_completed == 2


def test_run_executor_mock_fixture_end_to_end(tmp_path):
    """Test Run Executor Mock Fixture End To End for this module."""
    plan = load_execution_plan(FIXTURE_PLAN)
    result = run_executor(
        plan, str(tmp_path), gateway=_test_gateway(tmp_path), llm=_mock_llm_pass()
    )
    assert "EXEC_MARKER" in (tmp_path / "hello.txt").read_text(encoding="utf-8")
    assert "VERSION" in (tmp_path / "app.py").read_text(encoding="utf-8")
    assert result.steps_completed == 3


def test_executor_cli_runs_fixture(tmp_path):
    """Test Executor Cli Runs Fixture for this module."""
    import os

    env = os.environ.copy()
    env["CURSOR_API_KEY"] = ""
    env.update(_validation_stub_env())
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
        env=env,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["steps_completed"] == 3
    assert (tmp_path / "hello.txt").exists()
    assert "[executor]" in result.stderr
    assert "step=1" in result.stderr or "step=1 " in result.stderr


def test_executor_dry_run_cli():
    """Test Executor Dry Run Cli for this module."""
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


def test_executor_allows_read_verify_on_out_of_scope_files(tmp_path):
    """Verify-only plan steps may touch files outside files_to_change."""
    change_file = tmp_path / "change.py"
    change_file.write_text("a = 1\n", encoding="utf-8")
    verify_file = tmp_path / "verify_only.py"
    verify_file.write_text("weak_hash = True\n", encoding="utf-8")

    plan = ExecutionPlan(
        ticket_id="VERIFY-ONLY-1",
        root_hypothesis="patch one file; verify another without editing",
        files_to_change=["change.py"],
        create_files=False,
        risk_level="medium",
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                file="verify_only.py",
                target="ground weak hash usage",
                reason="verify-only hypothesis",
                validate="file readable",
            ),
            ExecutionStep(
                step_id=2,
                action="VERIFY",
                file="verify_only.py",
                target="confirm weak hash still present",
                reason="verify-only mitigation",
                validate="file exists",
            ),
        ],
    )
    result = run_executor(
        plan, str(tmp_path), gateway=_test_gateway(tmp_path), llm=_mock_llm_pass()
    )
    assert result.steps_completed == 2


def test_verify_passes_when_pem_only_in_comments_after_intent_marker(tmp_path):
    """VERIFY must not fail when PEM headers appear only in comment placeholders."""
    from src.agents.executor.agent import _execute_verify
    from src.agents.executor.tools.builder import build_executor_tools

    target = tmp_path / ".ssh" / "wrongsecrets.keys"
    target.parent.mkdir(parents=True)
    target.write_text(
        "# INTENT_MARKER:SPACE-129070_REMOVE_SSH_PRIVATE_KEY\n"
        "# Placeholder (non-sensitive):\n"
        "# -----BEGIN OPENSSH PRIVATE KEY-----\n"
        "# [REDACTED]\n"
        "# -----END OPENSSH PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SPACE-129070",
        root_hypothesis="remove ssh key",
        files_to_change=[".ssh/wrongsecrets.keys"],
        execution_steps=[
            ExecutionStep(
                step_id=2,
                action="EDIT",
                file=".ssh/wrongsecrets.keys",
                target="replace key",
                reason="fix",
                validate="patched",
                intent_marker="INTENT_MARKER:SPACE-129070_REMOVE_SSH_PRIVATE_KEY",
                old_string="-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----\n",
                new_string="# INTENT_MARKER:SPACE-129070_REMOVE_SSH_PRIVATE_KEY\n",
                change="redact",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                file=".ssh/wrongsecrets.keys",
                target="verify",
                reason="verify",
                validate="no live pem",
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
    assert ok, out


def test_executor_syncs_edit_file_into_allowlist(tmp_path):
    """EDIT-step paths are unioned into files_to_change before the allowlist check."""
    target = tmp_path / "forbidden.py"
    target.write_text("x = 1\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="BLOCK-EDIT-1",
        root_hypothesis="must not edit outside whitelist",
        files_to_change=["other.py"],
        create_files=False,
        risk_level="high",
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file="forbidden.py",
                target="patch",
                reason="disallowed",
                validate="patched",
                old_string="x = 1\n",
                new_string="x = 2\n",
                intent_marker="x = 2",
            ),
        ],
    )
    result = run_executor(
        plan, str(tmp_path), gateway=_test_gateway(tmp_path), llm=_mock_llm_pass()
    )
    assert result.steps_completed == 1
    assert "forbidden.py" in result.changed_files


def test_executor_syncs_files_to_change_from_edit_steps(tmp_path):
    """Executor repairs incomplete files_to_change before enforcing the edit allowlist."""
    ssh_dir = tmp_path / ".ssh"
    ssh_dir.mkdir()
    ssh_file = ssh_dir / "wrongsecrets.keys"
    ssh_file.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----\n",
        encoding="utf-8",
    )
    change_file = tmp_path / "app.properties"
    change_file.write_text("password=hardcoded\n", encoding="utf-8")

    plan = ExecutionPlan(
        ticket_id="SPACE-129070",
        root_hypothesis="remove secrets",
        files_to_change=["app.properties"],
        create_files=False,
        risk_level="high",
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                file="app.properties",
                target="read",
                reason="ground",
                validate="ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                file="app.properties",
                target="parameterize",
                reason="fix",
                validate="grep ${",
                target_type="assignment",
                target_name="password",
                approximate_line=1,
                instruction="Use env var for password",
                intent_marker="${",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                file="app.properties",
                target="verify",
                reason="check",
                validate="grep ${",
                intent_marker="${",
            ),
            ExecutionStep(
                step_id=4,
                action="READ",
                file=".ssh/wrongsecrets.keys",
                target="read key",
                reason="ground",
                validate="ok",
            ),
            ExecutionStep(
                step_id=5,
                action="EDIT",
                file=".ssh/wrongsecrets.keys",
                target="redact",
                reason="remove key",
                validate="grep REMOVED_FOR_SECURITY",
                target_type="file",
                instruction="Replace key with placeholder",
                intent_marker="REMOVED_FOR_SECURITY",
            ),
            ExecutionStep(
                step_id=6,
                action="VERIFY",
                file=".ssh/wrongsecrets.keys",
                target="verify",
                reason="check",
                validate="grep REMOVED_FOR_SECURITY",
                intent_marker="REMOVED_FOR_SECURITY",
            ),
        ],
    )
    result = run_executor(
        plan, str(tmp_path), gateway=_test_gateway(tmp_path), llm=_mock_llm_pass()
    )
    assert result.steps_completed == 6
    assert ".ssh/wrongsecrets.keys" in result.changed_files
