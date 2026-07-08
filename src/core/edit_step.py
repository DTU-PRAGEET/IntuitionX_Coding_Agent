"""Helpers for instruction-based EDIT steps — no symbol resolution."""

from __future__ import annotations

from typing import Any

from src.core.schemas import ExecutionStep


def is_instruction_edit(step: ExecutionStep | dict[str, Any]) -> bool:
    """True when step uses an instruction-based EDIT (not legacy patches)."""
    if isinstance(step, ExecutionStep):
        return bool((step.instruction or "").strip())
    return bool((step.get("instruction") or "").strip())
