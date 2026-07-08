"""Tests for executor edit instruction passthrough."""

from __future__ import annotations

from src.agents.executor.agent import _build_edit_instruction
from src.core.schemas import ExecutionPlan


def test_build_edit_instruction_passes_instruction_verbatim():
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["f.py"],
        execution_steps=[],
        risk_level="low",
    )
    step = {
        "file": "f.py",
        "reason": "security",
        "instruction": "Fix the bug. Acceptance: (1) fixed, (2) tests pass.",
        "intent_marker": "FIXED",
    }
    text = _build_edit_instruction(step, plan)
    assert "Fix the bug. Acceptance: (1) fixed, (2) tests pass." in text
    assert "Acceptance checklist (self-verify" not in text
    assert "Mechanical constraint" in text


def test_build_edit_instruction_includes_caution_flag():
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=["f.py"],
        execution_steps=[],
        risk_level="low",
    )
    step = {
        "file": "f.py",
        "reason": "security",
        "instruction": "Fix sanitizer bypass. Acceptance: (1) sanitizer used.",
        "intent_marker": "bypassSecurityTrustHtml",
        "target_type": "variable",
        "target_name": "this.searchValue",
        "caution_flag": (
            "CAUTION: 'this.searchValue' appears 4 times; fix ngOnInit assignment only."
        ),
    }
    text = _build_edit_instruction(step, plan)
    assert "Ambiguity caution" in text
    assert "this.searchValue' appears 4 times" in text
    assert "Semantic target — target_type: variable" in text
    assert "NOT an error" in text
