"""Shared types + path-normalization for evaluators."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Literal, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from evals.harness.prediction import Prediction
    from evals.schema.case import EvalCase


@dataclass
class EvalScore:
    """Single evaluator metric (0–1) for one case; optional applicability flag.
    Produced by functions in `evaluators/scoring.py`; aggregated in `report.py`."""
    key: str
    score: float                 # 0.0 .. 1.0
    comment: str = ""
    applicable: bool = True      # False -> excluded from aggregates (e.g. not in scope)


@dataclass
class EvaluatorSpec:
    """Registry entry binding evaluator key, scope, scoring fn, and pass threshold.
    Collected in `evaluators/__init__.py`; consumed by `runners/run_eval.score_case`."""
    key: str
    scope: str                   # routing | research | plan | implement
    fn: "Callable[..., EvalScore]"
    threshold: float = 1.0       # per-case pass cutoff
    kind: Literal["deterministic", "llm_judge"] = "deterministic"
    requires_artifact: bool = False


def normalize_path(p: str) -> str:
    """Collapse path variants seen across agents (./, leading /, backslashes).

    Matches the real-world drift logged in docs/Solved_errors.md (pseudo-absolute
    `/sqli/...`, `./` prefixes). Compares on the trailing path tail so a correct
    file found at a different relative root still counts.
    """
    p = (p or "").strip().replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    p = p.lstrip("/")
    return p


def path_tail(p: str, depth: int = 3) -> str:
    """Last `depth` segments — tolerant matching of equivalent paths."""
    parts = [seg for seg in normalize_path(p).split("/") if seg]
    return "/".join(parts[-depth:])
