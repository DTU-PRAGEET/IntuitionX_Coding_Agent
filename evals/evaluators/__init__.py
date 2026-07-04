"""Deterministic evaluators (Milestone 2.1 scoring rubrics).

Each evaluator is a pure function (case, prediction) -> EvalScore. They are
registered in EVALUATORS with the eval_scope they belong to and a per-case pass
threshold. The same functions are reused by the LangSmith adapter in
evals.langsmith_adapter.
"""

from evals.evaluators.base import EvalScore, EvaluatorSpec
from evals.evaluators.scoring import (
    file_identification_f1,
    fix_type_match,
    patch_contains_excludes,
    plan_line_overlap,
    route_correctness,
)

EVALUATORS: list[EvaluatorSpec] = [
    EvaluatorSpec("route_correct", "routing", route_correctness, threshold=1.0),
    EvaluatorSpec("file_f1", "research", file_identification_f1, threshold=0.5),
    EvaluatorSpec("fix_type_match", "plan", fix_type_match, threshold=1.0),
    EvaluatorSpec("plan_line_overlap", "plan", plan_line_overlap, threshold=0.5),
    EvaluatorSpec("patch_contains", "implement", patch_contains_excludes, threshold=1.0),
]

__all__ = [
    "EvalScore",
    "EvaluatorSpec",
    "EVALUATORS",
    "file_identification_f1",
    "fix_type_match",
    "patch_contains_excludes",
    "plan_line_overlap",
    "route_correctness",
]
