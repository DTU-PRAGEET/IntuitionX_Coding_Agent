"""Graph run result types (no LangGraph imports)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

RunKind = Literal["completed", "paused", "failed"]


@dataclass
class GraphRunOutcome:
    """Graph Run Outcome.
    Encapsulates related behavior for the Graph Run Outcome component.
    """
    kind: RunKind
    state: dict[str, Any]
    gate: str | None = None
    error: str | None = None
