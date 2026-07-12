"""Tests for whole-plan two-phase batch edit flow."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.agents.executor.agent import run_executor
from src.core.schemas import ExecutionPlan, ExecutionStep
from src.integrations.cursor.gateway import LocalPatchCursorGateway


def _mock_llm_pass():
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content=json.dumps({
            "pass": True,
            "confidence": 0.99,
            "reason": "ok",
            "missing_requirements": [],
        })
    )
    return llm


def _semantic_edit(step_id: int, file: str, instruction: str, marker: str) -> ExecutionStep:
    return ExecutionStep(
        step_id=step_id,
        action="EDIT",
        file=file,
        target=instruction[:40],
        reason="fix",
        validate=f"grep {marker}",
        target_type="block",
        target_name=f"target_{step_id}",
        approximate_line=1,
        end_line=5,
        instruction=instruction,
        intent_marker=marker,
    )


def test_chained_same_file_edits_use_single_batch_call(tmp_path):
    """Multiple EDIT steps on the same file are sent in one batch pass #1 call."""
    target = tmp_path / "app.ts"
    target.write_text("const a = 1;\nconst b = 2;\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix both",
        files_to_change=["app.ts"],
        execution_steps=[
            _semantic_edit(1, "app.ts", "Rename a to alpha", "alpha"),
            _semantic_edit(2, "app.ts", "Rename b to beta", "beta"),
        ],
        risk_level="low",
    )

    batch_sizes: list[int] = []

    class BatchSizeGateway(LocalPatchCursorGateway):
        def edit(self, *args, **kwargs):
            meta = kwargs.get("metadata") or {}
            edits = meta.get("edits") or []
            batch_sizes.append(len(edits))
            return super().edit(*args, **kwargs)

    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content=json.dumps({
            "pass": True,
            "confidence": 0.99,
            "reason": "ok",
            "missing_requirements": [],
        })
    )

    result = run_executor(
        plan,
        str(tmp_path),
        llm=llm,
        gateway=BatchSizeGateway(tmp_path),
    )

    assert result.steps_completed == 2
    assert batch_sizes == [2]
    assert llm.invoke.call_count == 2


def test_repair_batch_only_includes_failed_steps(tmp_path):
    """Batch pass #2 sends only steps that failed pass #1 validation."""
    (tmp_path / "a.py").write_text("bad_a\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("bad_b\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix both files",
        files_to_change=["a.py", "b.py"],
        execution_steps=[
            _semantic_edit(1, "a.py", "Fix a", "FIXED_A"),
            _semantic_edit(2, "b.py", "Fix b", "FIXED_B"),
        ],
        risk_level="low",
    )

    batch_pass_sizes: list[int] = []

    class BatchSizeGateway(LocalPatchCursorGateway):
        def edit(self, *args, **kwargs):
            meta = kwargs.get("metadata") or {}
            edits = meta.get("edits") or []
            batch_pass_sizes.append(len(edits))
            return super().edit(*args, **kwargs)

    llm = MagicMock()
    llm.invoke.side_effect = [
        MagicMock(content=json.dumps({
            "pass": False,
            "confidence": 0.1,
            "reason": "a missing",
            "missing_requirements": ["Fix a"],
        })),
        MagicMock(content=json.dumps({
            "pass": True,
            "confidence": 0.99,
            "reason": "b ok",
            "missing_requirements": [],
        })),
        MagicMock(content=json.dumps({
            "pass": True,
            "confidence": 0.99,
            "reason": "a fixed",
            "missing_requirements": [],
        })),
    ]

    result = run_executor(
        plan,
        str(tmp_path),
        llm=llm,
        gateway=BatchSizeGateway(tmp_path),
    )

    assert result.steps_completed == 2
    assert batch_pass_sizes == [2, 1]
    assert any("repair" in d for d in result.plan_deviations)


def test_aggregate_failure_lists_all_steps(tmp_path):
    """When multiple steps fail after pass 2, result records every failing step."""
    (tmp_path / "a.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("y\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["a.py", "b.py"],
        execution_steps=[
            _semantic_edit(1, "a.py", "Fix a", "A"),
            _semantic_edit(2, "b.py", "Fix b", "B"),
        ],
        risk_level="low",
    )

    fail_payload = json.dumps({
        "pass": False,
        "confidence": 0.1,
        "reason": "still broken",
        "missing_requirements": ["not fixed"],
    })
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content=fail_payload)

    result = run_executor(
        plan,
        str(tmp_path),
        llm=llm,
        gateway=LocalPatchCursorGateway(tmp_path),
    )

    assert result.validation_failed_steps == [1, 2]
    assert "step 1" in result.validation_warning
    assert "step 2" in result.validation_warning
    assert "2 step(s) still failing" in result.validation_warning


def test_read_delegated_after_batch_deletes_file(tmp_path):
    """READ steps must not fail when batch edit removes the file before the step loop."""
    key_path = tmp_path / ".ssh" / "wrongsecrets.keys"
    key_path.parent.mkdir(parents=True)
    key_path.write_text(
        "-----BEGIN OPENSSH PRIVATE KEY-----\nsecret\n-----END OPENSSH PRIVATE KEY-----\n",
        encoding="utf-8",
    )

    class DeleteKeyGateway(LocalPatchCursorGateway):
        def edit(self, *args, **kwargs):
            result = super().edit(*args, **kwargs)
            if key_path.is_file():
                key_path.unlink()
            return result

    plan = ExecutionPlan(
        ticket_id="SPACE-129070",
        root_hypothesis="remove ssh key",
        files_to_change=[".ssh/wrongsecrets.keys"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                file=".ssh/wrongsecrets.keys",
                target="read key",
                reason="cursor context",
                validate="ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                file=".ssh/wrongsecrets.keys",
                target="remove key",
                reason="remove committed private key",
                validate="grep PLACEHOLDER",
                instruction="Remove private key; add placeholder example file.",
                intent_marker="PLACEHOLDER",
                target_type="file",
                change="delete key material",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                file=".ssh/wrongsecrets.keys",
                target="verify removal",
                reason="ensure key removed",
                validate="grep -v BEGIN OPENSSH PRIVATE KEY",
            ),
        ],
        risk_level="high",
    )

    result = run_executor(
        plan,
        str(tmp_path),
        llm=_mock_llm_pass(),
        gateway=DeleteKeyGateway(tmp_path),
    )

    assert result.steps_completed == 3
    read_result = next(r for r in result.step_results if r["action"] == "READ")
    assert "delegated" in read_result["output"]
    verify_result = next(r for r in result.step_results if r["action"] == "VERIFY")
    assert verify_result["ok"] is True
    assert not key_path.is_file()
