"""Integration tests for structural pre-validation behavior."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

from src.agents.executor.agent import _evaluate_post_edit
from src.agents.executor.tools.builder import build_executor_tools
from src.agents.executor.validation import capture_pre_edit
from src.core.repo_snapshot import file_content_hash
from src.core.schemas import ExecutionPlan, ExecutionStep


def test_ts_structural_mismatch_defers_to_llm(tmp_path):
    """Brace imbalance in .tsx does not auto-fail — LLM receives structural_warnings."""
    target = tmp_path / "App.tsx"
    target.write_text("const x = 1;\n", encoding="utf-8")
    step = ExecutionStep(
        step_id=1,
        action="EDIT",
        file="App.tsx",
        target="fix",
        reason="r",
        validate="ok",
        target_type="block",
        target_name="object_literal",
        approximate_line=1,
        end_line=2,
        instruction="Add object literal",
        intent_marker="object",
    ).model_dump(by_alias=True)
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["App.tsx"],
        execution_steps=[],
        risk_level="low",
    )

    llm = MagicMock()
    llm.invoke.return_value = MagicMock(
        content=json.dumps({
            "pass": True,
            "confidence": 0.95,
            "reason": "Object literal present",
            "missing_requirements": [],
        })
    )

    registry, _ = build_executor_tools(str(tmp_path))
    try:
        pre_edit = capture_pre_edit(str(tmp_path), step)
        pre_hash = file_content_hash(str(tmp_path), "App.tsx")
        # Unclosed brace triggers structural checker; LLM decides pass/fail
        target.write_text("const x = {\n  value: 1\n", encoding="utf-8")
        post_hash = file_content_hash(str(tmp_path), "App.tsx")

        result = _evaluate_post_edit(
            registry,
            step,
            plan,
            llm=llm,
            pre_edit=pre_edit,
            policy="policy",
            prior_failure=None,
            validation_attempt=1,
            pre_hash=pre_hash,
            post_hash=post_hash,
            cursor_called=True,
        )
    finally:
        registry.dispose()

    assert result.passed is True
    llm.invoke.assert_called_once()
    prompt = llm.invoke.call_args[0][0]
    assert "structural_warnings" in prompt
