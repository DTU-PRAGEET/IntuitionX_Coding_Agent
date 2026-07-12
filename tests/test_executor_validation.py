"""Tests for executor LLM semantic validation module."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from src.agents.executor.validation import (
    ValidationResult,
    capture_pre_edit,
    load_validation_policy,
    read_modified_snippet,
    read_snippet_at_span,
    validate_edit,
)
from src.core.schemas import ExecutionPlan, ExecutionStep


def _llm_response(payload: dict) -> MagicMock:
    mock = MagicMock()
    mock.content = json.dumps(payload)
    return mock


def _semantic_step(**kw) -> dict:
    base = ExecutionStep(
        step_id=1,
        action="EDIT",
        file="app.py",
        target="fix",
        reason="security fix",
        validate="grep for SAFE",
        target_type="block",
        target_name="secret",
        approximate_line=1,
        end_line=3,
        instruction="Replace hardcoded secret with env var",
        intent_marker="SAFE",
    ).model_dump(by_alias=True)
    base.update(kw)
    return base


def test_validation_prompt_scoped_as_edit_verifier():
    """Prompt must scope validation to the requested edit, not whole-file security."""
    from src.agents.executor.validation import _build_validation_prompt

    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["app.properties"],
        execution_steps=[],
        risk_level="high",
    )
    step = _semantic_step(
        instruction="Replace top-level password with ${PASSWORD:default}",
        intent_marker="${PASSWORD",
    )
    prompt = _build_validation_prompt(
        "policy",
        plan,
        step,
        original_snippet="password=hardcoded",
        modified_snippet="password=${PASSWORD:changeme}",
    )
    assert "SECURITY-INTENT VERIFIER" in prompt
    assert "security property" in prompt
    assert "NOT to audit the entire file" not in prompt
    assert "observations" in prompt
    assert "Do NOT fail because of" in prompt
    assert "equivalent" in prompt.lower()


def test_validation_prompt_includes_structural_warnings():
    """Structural warnings for JS/TS are passed to the LLM, not hard-failed."""
    from src.agents.executor.validation import _build_validation_prompt

    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["app.tsx"],
        execution_steps=[],
        risk_level="low",
    )
    step = _semantic_step(file="app.tsx")
    prompt = _build_validation_prompt(
        "policy",
        plan,
        step,
        original_snippet="const x = 1;",
        modified_snippet="const x = `${1}`;",
        structural_warnings=["unclosed '{'"],
    )
    assert "structural_warnings" in prompt
    assert "unclosed '{'" in prompt
    assert "false positives" in prompt


def test_validate_edit_accepts_pass_with_out_of_scope_observations():
    """Observations in LLM response are captured but do not block pass."""
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["app.py"],
        execution_steps=[],
        risk_level="low",
    )
    llm = MagicMock()
    llm.invoke.return_value = _llm_response(
        {
            "pass": True,
            "confidence": 0.92,
            "reason": "Password property updated as requested",
            "missing_requirements": [],
            "observations": ["ctf_key hardcoded elsewhere in file — out of scope for this step"],
        }
    )
    result = validate_edit(
        llm,
        plan,
        _semantic_step(),
        original_snippet="password=secret",
        modified_snippet="password=${PASSWORD:default}",
        validation_policy="policy",
        attempt=1,
    )
    assert result.passed is True
    assert result.observations
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["app.py"],
        execution_steps=[],
        risk_level="low",
    )
    llm = MagicMock()
    llm.invoke.return_value = _llm_response(
        {
            "pass": True,
            "confidence": 0.95,
            "reason": "Env var used instead of hardcoded value",
            "missing_requirements": [],
        }
    )
    result = validate_edit(
        llm,
        plan,
        _semantic_step(),
        original_snippet="SECRET = 'abc'",
        modified_snippet="SECRET = os.getenv('SECRET')",
        validation_policy="Validate edits strictly.",
        attempt=1,
    )
    assert result.passed is True
    assert result.confidence == 0.95
    prompt = llm.invoke.call_args[0][0]
    assert "SECRET = 'abc'" in prompt
    assert "os.getenv" in prompt
    assert "Validate edits strictly." in prompt


def test_validate_edit_parses_fail_response():
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["app.py"],
        execution_steps=[],
        risk_level="low",
    )
    llm = MagicMock()
    llm.invoke.return_value = _llm_response(
        {
            "pass": False,
            "confidence": 0.2,
            "reason": "Hardcoded secret still present",
            "missing_requirements": ["Remove literal secret"],
        }
    )
    result = validate_edit(
        llm,
        plan,
        _semantic_step(),
        original_snippet="SECRET = 'abc'",
        modified_snippet="SECRET = 'abc'",
        validation_policy="policy",
        attempt=1,
    )
    assert result.passed is False
    assert result.missing_requirements == ["Remove literal secret"]


def test_validate_edit_malformed_json_returns_failure():
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["app.py"],
        execution_steps=[],
        risk_level="low",
    )
    llm = MagicMock()
    llm.invoke.return_value = MagicMock(content="not json at all")
    result = validate_edit(
        llm,
        plan,
        _semantic_step(),
        original_snippet="a",
        modified_snippet="b",
        validation_policy="policy",
        attempt=1,
    )
    assert result.passed is False
    assert "unparseable" in result.reason


def test_read_snippet_at_span_with_padding(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("line1\nline2\nline3\nline4\nline5\n", encoding="utf-8")
    snippet = read_snippet_at_span(str(tmp_path), "app.py", 2, 3, padding=1)
    assert snippet == "line1\nline2\nline3\nline4"


def test_capture_pre_edit_semantic_block(tmp_path):
    target = tmp_path / "handler.py"
    target.write_text(
        "def foo():\n    x = 1\n    return x\n",
        encoding="utf-8",
    )
    step = _semantic_step(
        file="handler.py",
        target_type="function",
        target_name="foo",
        approximate_line=1,
    )
    pre = capture_pre_edit(str(tmp_path), step)
    assert "def foo" in pre.original_snippet
    assert pre.start_line >= 1


def test_read_modified_snippet_after_edit(tmp_path):
    target = tmp_path / "app.py"
    target.write_text("old\nmiddle\nend\n", encoding="utf-8")
    step = _semantic_step(file="app.py", approximate_line=1, end_line=3)
    pre = capture_pre_edit(str(tmp_path), step)
    target.write_text("new\nmiddle\nend\n", encoding="utf-8")
    modified = read_modified_snippet(str(tmp_path), step, pre)
    assert "new" in modified


def test_load_validation_policy_includes_validation_policy_reference():
    policy = load_validation_policy()
    assert "REFERENCE: validation_policy" in policy
    assert "Progressive validation flow" in policy
