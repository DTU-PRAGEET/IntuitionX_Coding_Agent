"""Tests for planner patch grounding — replaced by semantic target resolution."""

from __future__ import annotations

from src.core.schemas import ExecutionStep
from src.core.semantic_target import resolve_semantic_target


def test_semantic_resolve_from_evidence_path(tmp_path):
    rel = "sqli/app.py"
    full = tmp_path / rel
    full.parent.mkdir(parents=True)
    full.write_text("def setup_jinja(app, autoescape=False):\n    pass\n", encoding="utf-8")

    step = ExecutionStep(
        step_id=1,
        action="EDIT",
        file=rel,
        target="fix jinja",
        reason="xss",
        validate="v",
        target_type="function",
        target_name="setup_jinja",
        instruction="Enable autoescape",
        intent_marker="autoescape=True",
    )
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.start_line == 1
    assert "setup_jinja" in resolved.snippet
