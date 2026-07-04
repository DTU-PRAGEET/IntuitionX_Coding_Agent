"""Tests for executor semantic edit validation."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.agents.executor.agent import _execute_edit, run_executor
from src.agents.executor.tools.builder import build_executor_tools
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
        target_name=kw.get("target_name", ""),
        approximate_line=kw.get("approximate_line", 1),
        end_line=kw.get("end_line", 10),
        instruction=instruction,
        intent_marker=marker,
    )


def test_executor_rejects_failed_cursor_edit(tmp_path):
    """Failed edit when Cursor fails and LLM validation also fails."""
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
    registry, _ = build_executor_tools(str(tmp_path))
    try:
        ok, out, _devs = _execute_edit(
            registry,
            plan.execution_steps[0].model_dump(),
            plan,
            create_files=False,
            llm=_llm_fail(),
            gateway=FailGateway(tmp_path),
        )
    finally:
        registry.dispose()
    assert ok is False


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
                file="f.py",
                target="read",
                reason="r",
                validate="ok",
            ),
        ],
        risk_level="low",
    )
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
    """First validation fails, second passes — exactly two Cursor calls."""
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

    registry, _ = build_executor_tools(str(tmp_path))
    try:
        ok, _out, devs = _execute_edit(
            registry,
            plan.execution_steps[0].model_dump(),
            plan,
            create_files=False,
            llm=llm,
            gateway=CountingGateway(tmp_path),
        )
    finally:
        registry.dispose()

    assert ok is True
    assert call_count["n"] == 2
    assert llm.invoke.call_count == 2
    assert any("retry" in d for d in devs)


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
    registry, _ = build_executor_tools(str(tmp_path))
    try:
        ok, out, _ = _execute_edit(
            registry,
            plan.execution_steps[0].model_dump(),
            plan,
            create_files=False,
            llm=llm,
            gateway=CountingGateway(tmp_path),
        )
    finally:
        registry.dispose()

    assert ok is False
    assert call_count["n"] == 2
    assert llm.invoke.call_count == 2
    assert "incomplete" in out.lower()
