"""Deterministic preflight — fatal vs warning classification for hybrid eval."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from evals.evaluators.base import EvalScore

if TYPE_CHECKING:
    from evals.artifacts.pipeline_result import PipelineArtifact
    from evals.harness.prediction import Prediction
    from evals.schema.case import EvalCase


@dataclass
class PreflightResult:
    """Outcome of deterministic preflight for one agent scope."""

    scope: str
    score: float
    fatals: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    passed: bool = True
    skip_judge: bool = False

    @property
    def has_fatals(self) -> bool:
        return bool(self.fatals)


def planner_preflight(case: "EvalCase", artifact: "PipelineArtifact") -> PreflightResult:
    """Planner deterministic checks — fatals skip judge; warnings go to judge context."""
    if "plan" not in case.eval_scope:
        return PreflightResult("plan", 1.0, passed=True, skip_judge=False)

    fatals: list[str] = []
    warnings: list[str] = []

    if artifact.stage_failed == "planner" or (
        artifact.stage_failed in ("orchestrator", "research") and artifact.plan is None
    ):
        fatals.append(f"pipeline failed before/during planner: {artifact.error or artifact.stage_failed}")

    if artifact.plan is None and not fatals:
        fatals.append("no execution plan produced")

    pv = artifact.plan_validation or {}
    for f in pv.get("fatals") or []:
        if f not in fatals:
            fatals.append(str(f))
    for w in pv.get("warnings") or []:
        if w not in warnings:
            warnings.append(str(w))

    if artifact.plan:
        steps = artifact.plan.get("execution_steps") or []
        edits = [s for s in steps if (s.get("action") or "").upper() == "EDIT"]
        if not edits and case.expected_fix_type:
            warnings.append("plan has no EDIT steps but fix expected")
        for step in edits:
            if step.get("instruction") and not step.get("intent_marker"):
                warnings.append(
                    f"EDIT step {step.get('step_id')}: missing intent_marker"
                )

    for flag in pv.get("flags") or []:
        warnings.append(f"plan flag: {flag}")

    skip_judge = bool(fatals)
    score = 0.0 if fatals else max(0.0, 1.0 - 0.05 * len(warnings))

    return PreflightResult(
        scope="plan",
        score=round(score, 4),
        fatals=fatals,
        warnings=warnings,
        passed=not fatals,
        skip_judge=skip_judge,
    )


def _is_edit_validation_failure(error: str | None) -> bool:
    """True when executor stopped only because LLM edit validation failed."""
    if not error:
        return False
    return "Executor edit validation failed" in error


def executor_preflight(case: "EvalCase", artifact: "PipelineArtifact") -> PreflightResult:
    """Executor deterministic checks."""
    if "implement" not in case.eval_scope:
        return PreflightResult("implement", 1.0, passed=True, skip_judge=False)

    fatals: list[str] = []
    warnings: list[str] = []

    if artifact.stage_failed == "executor":
        if _is_edit_validation_failure(artifact.error):
            warnings.append(
                f"executor edit validation failed (non-fatal for judge): "
                f"{(artifact.error or '')[:240]}"
            )
        else:
            fatals.append(f"executor failed: {artifact.error or 'unknown'}")

    plan_files = set()
    if artifact.plan:
        plan_files = set(artifact.plan.get("files_to_change") or [])
        edit_count = sum(
            1
            for s in artifact.plan.get("execution_steps") or []
            if (s.get("action") or "").upper() == "EDIT"
        )
        if edit_count > 0 and not artifact.dry_run and not artifact.git_diff.strip():
            fatals.append("expected git diff but diff is empty")

    if artifact.execution:
        for sr in artifact.step_results or []:
            if (sr.get("action") or "").upper() == "EDIT" and not sr.get("ok"):
                warnings.append(
                    f"EDIT step {sr.get('step_id')} ok=False: {(sr.get('output') or '')[:80]}"
                )

    changed = set(artifact.changed_files or [])
    if plan_files and changed:
        extra = changed - plan_files
        if extra:
            devs = (artifact.execution or {}).get("plan_deviations") or []
            if not devs:
                warnings.append(f"changed files outside plan: {sorted(extra)}")

    for val in artifact.executor_validations or []:
        conf = float(val.get("confidence") or 1.0)
        if not val.get("passed", True):
            warnings.append(
                f"EDIT step {val.get('step_id')} validation failed: "
                f"{(val.get('reason') or '')[:120]}"
            )
        elif conf < 0.7:
            warnings.append(
                f"low validation confidence ({conf:.2f}): {val.get('reason', '')[:80]}"
            )

    skip_judge = bool(fatals)
    score = 0.0 if fatals else max(0.0, 1.0 - 0.1 * len(warnings))

    return PreflightResult(
        scope="implement",
        score=round(score, 4),
        fatals=fatals,
        warnings=warnings,
        passed=not fatals,
        skip_judge=skip_judge,
    )


def preflight_for_artifact(
    case: "EvalCase",
    artifact: "PipelineArtifact",
) -> dict[str, PreflightResult]:
    """Run all applicable preflight checks."""
    out: dict[str, PreflightResult] = {}
    if "plan" in case.eval_scope:
        out["plan"] = planner_preflight(case, artifact)
    if "implement" in case.eval_scope:
        out["implement"] = executor_preflight(case, artifact)
    return out


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
