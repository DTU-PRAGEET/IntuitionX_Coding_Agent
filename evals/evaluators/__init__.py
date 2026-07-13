"""Deterministic evaluators (Milestone 2.1) + LLM judge evaluators (semantic pipeline).

Routing and research evaluators are unchanged. Plan/implement use hybrid
preflight + LLM judges for integration eval; legacy patch evaluators live in
legacy_scoring.py for offline fixture rescoring.
"""

from evals.evaluators.base import EvalScore, EvaluatorSpec
from evals.evaluators.executor_judge import executor_judge
from evals.evaluators.planner_judge import planner_judge
from evals.evaluators.preflight import executor_preflight_score, planner_preflight_score
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
    EvaluatorSpec(
        "planner_preflight", "plan", planner_preflight_score, threshold=0.90,
    ),
    EvaluatorSpec(
        "planner_judge", "plan", planner_judge, threshold=0.75,
        kind="llm_judge", requires_artifact=True,
    ),
    EvaluatorSpec(
        "executor_preflight", "implement", executor_preflight_score, threshold=0.90,
    ),
    EvaluatorSpec(
        "executor_judge", "implement", executor_judge, threshold=0.75,
        kind="llm_judge", requires_artifact=True,
    ),
]

LEGACY_EVALUATORS: list[EvaluatorSpec] = []

__all__ = [
    "EvalScore",
    "EvaluatorSpec",
    "EVALUATORS",
    "LEGACY_EVALUATORS",
    "file_identification_f1",
    "route_correctness",
    "planner_judge",
    "executor_judge",
    "planner_preflight_score",
    "executor_preflight_score",
    "fix_type_match",
    "plan_line_overlap",
    "patch_contains_excludes",
]
