"""Preflight evaluators exposed as EvalScore for score_case integration."""

from __future__ import annotations

from typing import TYPE_CHECKING

from evals.evaluators.base import EvalScore
from evals.evaluators.preflight import executor_preflight, planner_preflight

if TYPE_CHECKING:
    from evals.artifacts.pipeline_result import PipelineArtifact
    from evals.harness.prediction import Prediction
    from evals.schema.case import EvalCase


def _load_artifact(pred: "Prediction") -> "PipelineArtifact | None":
    if not pred.artifact_path:
        return None
    from evals.artifacts.pipeline_result import PipelineArtifact

    return PipelineArtifact.model_validate_json(
        open(pred.artifact_path, encoding="utf-8").read()
    )


def planner_preflight_score(case: "EvalCase", pred: "Prediction") -> EvalScore:
    """Deterministic planner preflight as EvalScore."""
    if "plan" not in case.eval_scope:
        return EvalScore("planner_preflight", 0.0, "not in scope", applicable=False)

    artifact = _load_artifact(pred)
    if artifact is None:
        return EvalScore("planner_preflight", 0.0, "no artifact", applicable=False)

    result = planner_preflight(case, artifact)
    comment = f"fatals={len(result.fatals)} warnings={len(result.warnings)}"
    if result.fatals:
        comment += f"; {result.fatals[0][:80]}"
    return EvalScore(
        "planner_preflight",
        result.score,
        comment,
        applicable=True,
    )


def executor_preflight_score(case: "EvalCase", pred: "Prediction") -> EvalScore:
    """Deterministic executor preflight as EvalScore."""
    if "implement" not in case.eval_scope:
        return EvalScore("executor_preflight", 0.0, "not in scope", applicable=False)

    artifact = _load_artifact(pred)
    if artifact is None:
        return EvalScore("executor_preflight", 0.0, "no artifact", applicable=False)

    result = executor_preflight(case, artifact)
    comment = f"fatals={len(result.fatals)} warnings={len(result.warnings)}"
    if result.fatals:
        comment += f"; {result.fatals[0][:80]}"
    return EvalScore(
        "executor_preflight",
        result.score,
        comment,
        applicable=True,
    )
