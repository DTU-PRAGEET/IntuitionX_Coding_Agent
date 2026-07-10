"""Tests for executor semantic edit validation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.agents.executor.agent import run_executor
from src.core.schemas import ExecutionPlan, ExecutionStep
from src.integrations.cursor.gateway import LocalPatchCursorGateway
from src.integrations.cursor.schemas import CursorEditResult


def _llm_pass(**extra) -> MagicMock:
    llm = MagicMock()
    payload = {
        "pass": True,
        "confidence": 0.98,
        "reason": "Fix applied",
        "missing_requirements": [],
        **extra,
    }
    llm.invoke.return_value = MagicMock(content=json.dumps(payload))
    return llm


def _llm_fail(**extra) -> MagicMock:
    llm = MagicMock()
    payload = {
        "pass": False,
        "confidence": 0.2,
        "reason": "Fix missing",
        "missing_requirements": ["Apply the instruction"],
        **extra,
    }
    llm.invoke.return_value = MagicMock(content=json.dumps(payload))
    return llm


def _semantic_edit(step_id: int, file: str, instruction: str, marker: str, **kw) -> ExecutionStep:
    return ExecutionStep(
        step_id=step_id,
        action="EDIT",
        file=file,
        target=instruction[:40],
        reason="security fix",
        validate=f"grep for {marker}",
        target_type=kw.get("target_type", "block"),
        target_name=kw.get("target_name", "target"),
        approximate_line=kw.get("approximate_line", 1),
        end_line=kw.get("end_line", 10),
        instruction=instruction,
        intent_marker=marker,
    )


def test_executor_rejects_failed_cursor_edit(tmp_path, monkeypatch):
    """Failed edit after only batch pass 1 still raises when repair is disabled."""
    from dataclasses import replace

    from src.agents.executor import agent as executor_agent
    from src.core.config import settings

    class FailGateway(LocalPatchCursorGateway):
        def edit(self, *args, **kwargs):
            return CursorEditResult(status="failed", text="failed", exit_code=1)

    monkeypatch.setattr(
        executor_agent,
        "settings",
        replace(settings, cursor_max_edit_attempts=1),
    )

    (tmp_path / "f.py").write_text("unchanged\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="bad",
        files_to_change=["f.py"],
        execution_steps=[_semantic_edit(1, "f.py", "Apply fix", "MISSING")],
        risk_level="low",
    )
    with pytest.raises(RuntimeError, match="still failing"):
        run_executor(
            plan,
            str(tmp_path),
            llm=_llm_fail(),
            gateway=FailGateway(tmp_path),
        )


def test_executor_soft_fails_after_batch_two_validation(tmp_path):
    """After batch pass 2 validation failure, executor returns partial result instead of raising."""

    class FailGateway(LocalPatchCursorGateway):
        def edit(self, *args, **kwargs):
            return CursorEditResult(status="failed", text="failed", exit_code=1)

    (tmp_path / "f.py").write_text("unchanged\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="bad",
        files_to_change=["f.py"],
        execution_steps=[_semantic_edit(1, "f.py", "Apply fix", "MISSING")],
        risk_level="low",
    )

    result = run_executor(
        plan,
        str(tmp_path),
        llm=_llm_fail(),
        gateway=FailGateway(tmp_path),
    )

    assert result.validation_failed_steps == [1]
    assert "still failing" in result.validation_warning


def test_executor_snapshot_mismatch_raises(tmp_path):
    from unittest.mock import patch

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
                file="f.py",
                target="read",
                reason="r",
                validate="ok",
            ),
        ],
        risk_level="low",
    )
    snap = {
        "repo_path": str(tmp_path),
        "cwd": "",
        "head": "cafebabe" * 5,
        "branch": "main",
        "status_short": "",
        "diff_stat": "",
        "is_git": True,
    }
    with patch("src.agents.executor.agent.git_repo_snapshot", return_value=snap):
        with pytest.raises(RuntimeError, match="snapshot mismatch"):
            run_executor(plan, str(tmp_path), gateway=LocalPatchCursorGateway(tmp_path))


def test_executor_accepts_semantic_edit_with_llm_validation(tmp_path):
    """Local semantic gateway applies edit; LLM validation confirms success."""
    target = tmp_path / "sqli" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text("setup_jinja(app, autoescape=False)\n", encoding="utf-8")

    plan = ExecutionPlan(
        ticket_id="SPACE-129069",
        root_hypothesis="Enable Jinja autoescape",
        files_to_change=["sqli/app.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                file="sqli/app.py",
                target="read app",
                reason="context",
                validate="ok",
            ),
            _semantic_edit(
                2,
                "sqli/app.py",
                "Enable autoescape=True in setup_jinja call",
                "autoescape=True",
                target_type="assignment",
                target_name="setup_jinja",
            ),
        ],
        risk_level="medium",
    )
    mock_llm = _llm_pass()
    result = run_executor(
        plan,
        str(tmp_path),
        llm=mock_llm,
        gateway=LocalPatchCursorGateway(tmp_path),
    )
    assert result.steps_completed == 2
    assert "autoescape=True" in target.read_text(encoding="utf-8")
    mock_llm.invoke.assert_called()


def test_executor_accepts_when_intent_already_present(tmp_path):
    """Cursor no-op with LLM validation pass when file already correct."""
    target = tmp_path / "sqli" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text(
        "setup_jinja(app, autoescape=True)  # intent:SPACE-129069-enable-autoescape\n",
        encoding="utf-8",
    )
    plan = ExecutionPlan(
        ticket_id="SPACE-129069",
        root_hypothesis="Enable Jinja autoescape",
        files_to_change=["sqli/app.py"],
        execution_steps=[
            _semantic_edit(
                1,
                "sqli/app.py",
                "Ensure autoescape enabled",
                "intent:SPACE-129069-enable-autoescape",
            ),
        ],
        risk_level="medium",
    )
    mock_llm = _llm_pass(reason="Fix already present")
    result = run_executor(
        plan,
        str(tmp_path),
        llm=mock_llm,
        gateway=LocalPatchCursorGateway(tmp_path),
    )
    assert result.steps_completed == 1
    mock_llm.invoke.assert_called()


def test_executor_retries_cursor_once_on_validation_failure(tmp_path):
    """First validation fails, second passes — exactly two Cursor batch calls."""
    target = tmp_path / "f.py"
    target.write_text("vulnerable = True\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["f.py"],
        execution_steps=[
            _semantic_edit(1, "f.py", "Set safe flag", "safe = True"),
        ],
        risk_level="low",
    )

    call_count = {"n": 0}

    class CountingGateway(LocalPatchCursorGateway):
        def edit(self, *args, **kwargs):
            call_count["n"] += 1
            return super().edit(*args, **kwargs)

    llm = MagicMock()
    llm.invoke.side_effect = [
        MagicMock(content=json.dumps({
            "pass": False,
            "confidence": 0.3,
            "reason": "Still vulnerable",
            "missing_requirements": ["Set safe flag"],
        })),
        MagicMock(content=json.dumps({
            "pass": True,
            "confidence": 0.95,
            "reason": "Fixed",
            "missing_requirements": [],
        })),
    ]

    result = run_executor(
        plan,
        str(tmp_path),
        llm=llm,
        gateway=CountingGateway(tmp_path),
    )

    assert result.steps_completed == 1
    assert call_count["n"] == 2
    assert llm.invoke.call_count == 2
    assert any("repair" in d for d in result.plan_deviations)


def test_executor_fails_without_third_cursor_call(tmp_path):
    """Two validation failures — no third Cursor invocation."""
    (tmp_path / "f.py").write_text("bad\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["f.py"],
        execution_steps=[_semantic_edit(1, "f.py", "Fix it", "FIXED")],
        risk_level="low",
    )

    call_count = {"n": 0}

    class CountingGateway(LocalPatchCursorGateway):
        def edit(self, *args, **kwargs):
            call_count["n"] += 1
            return super().edit(*args, **kwargs)

    llm = _llm_fail()
    result = run_executor(
        plan,
        str(tmp_path),
        llm=llm,
        gateway=CountingGateway(tmp_path),
    )

    assert call_count["n"] == 2
    assert llm.invoke.call_count == 2
    assert result.validation_failed_steps == [1]
    assert "step 1" in result.validation_warning


def test_executor_continues_after_validation_failure_in_eval_mode(tmp_path):
    """Eval mode returns structured result instead of raising on validation failure."""
    (tmp_path / "f.py").write_text("bad\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["f.py"],
        execution_steps=[
            _semantic_edit(1, "f.py", "Fix it", "FIXED"),
            ExecutionStep(
                step_id=2,
                action="VERIFY",
                file="f.py",
                target="verify",
                reason="check",
                validate="grep for FIXED",
            ),
        ],
        risk_level="low",
    )

    result = run_executor(
        plan,
        str(tmp_path),
        llm=_llm_fail(),
        gateway=LocalPatchCursorGateway(tmp_path),
        continue_on_edit_validation_failure=True,
    )

    assert result.validation_failed_steps == [1]
    assert len(result.step_results) == 2
    assert result.step_results[0]["ok"] is False
    assert result.step_results[0]["edit_outcome"]["passed"] is False
    assert result.step_results[1]["action"] == "VERIFY"
