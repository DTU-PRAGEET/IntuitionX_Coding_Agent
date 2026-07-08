"""Executor LLM-as-Judge evaluator."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from evals.evaluators.base import EvalScore
from evals.evaluators.llm_judge_base import (
    EXECUTOR_CRITERIA_WEIGHTS,
    JUDGE_VERSION,
    format_file_contexts,
    format_judge_comment,
    format_warnings,
    get_judge_llm,
    invoke_judge,
    load_cached_verdict,
    load_prompt,
    save_cached_verdict,
)
from evals.evaluators.preflight import executor_preflight

if TYPE_CHECKING:
    from evals.artifacts.pipeline_result import PipelineArtifact
    from evals.harness.prediction import Prediction
    from evals.schema.case import EvalCase


def _artifact_from_pred(pred: "Prediction") -> "PipelineArtifact | None":
    if not pred.artifact_path:
        return None
    from evals.artifacts.pipeline_result import PipelineArtifact

    return PipelineArtifact.model_validate_json(
        open(pred.artifact_path, encoding="utf-8").read()
    )


def _edit_steps(plan: dict | None) -> list[dict]:
    if not plan:
        return []
    return [
        s
        for s in plan.get("execution_steps") or []
        if (s.get("action") or "").upper() == "EDIT"
    ]


def build_executor_judge_prompt(case: "EvalCase", artifact: "PipelineArtifact", preflight) -> str:
    """Build user prompt for executor judge."""
    golden = artifact.golden or {}
    return f"""## Case + Plan
case_id: {case.case_id}
vuln_class: {golden.get('vuln_class', case.vuln_class)} / CWE: {golden.get('cwe', case.cwe)}
plan_edit_steps: {json.dumps(_edit_steps(artifact.plan), indent=2)[:6000]}

## Execution Results
changed_files: {json.dumps(artifact.changed_files)}
step_results: {json.dumps(artifact.step_results, indent=2)[:4000]}
plan_deviations: {json.dumps((artifact.execution or {}).get('plan_deviations', []))}
edit_outcomes: {json.dumps((artifact.plan_validation or {}).get('edit_outcomes', []), indent=2)[:3000]}
executor_validations: {json.dumps(artifact.executor_validations, indent=2)[:3000]}

## Before Context (±50 lines pre-edit)
{format_file_contexts(artifact.before_context)}

## After Context (±50 lines post-edit)
{format_file_contexts(artifact.after_context)}

## Diff
{artifact.git_diff[:8000] if artifact.git_diff else '(empty)'}

## Deterministic Checks + Warnings
preflight score: {preflight.score}
fatals: {json.dumps(preflight.fatals)}
{format_warnings(preflight.warnings)}

## Golden Hints (non-binding)
expected_contains: {json.dumps(golden.get('expected_patch_contains', case.expected_patch_contains))}
expected_excludes: {json.dumps(golden.get('expected_patch_excludes', case.expected_patch_excludes))}
pass_criteria: {json.dumps(golden.get('pass_criteria', case.pass_criteria))}

## Task
Score the EXECUTOR stage. Did it faithfully implement a correct security fix WITHOUT breaking intended functionality?
Pay special attention to regression_risk: fixes that delete functionality or return stubs instead of proper remediation.
Executor LLM validation failures in `executor_validations` are advisory context only — judge the final
repository state (diff + after context) independently. Do not auto-fail because the validator disagreed
with an equivalent implementation.
Return JSON:
{{
  "score": 0-100,
  "passed": bool,
  "criteria": {{
    "intent_implementation": {{"score": 0-100, "reason": ""}},
    "security_fix_quality": {{"score": 0-100, "reason": ""}},
    "code_correctness": {{"score": 0-100, "reason": ""}},
    "precision": {{"score": 0-100, "reason": ""}},
    "regression_risk": {{"score": 0-100, "reason": ""}},
    "validation_honesty": {{"score": 0-100, "reason": ""}},
    "response_accuracy": {{"score": 0-100, "reason": ""}}
  }},
  "hard_failures": [],
  "soft_failures": [],
  "reasoning": ""
}}"""


def executor_judge(
    case: "EvalCase",
    pred: "Prediction",
    *,
    llm=None,
    use_cache: bool = True,
    judge_version: str = JUDGE_VERSION,
) -> EvalScore:
    """Score executor via LLM judge (requires PipelineArtifact)."""
    if "implement" not in case.eval_scope:
        return EvalScore("executor_judge", 0.0, "not in scope", applicable=False)

    artifact = _artifact_from_pred(pred)
    if artifact is None:
        return EvalScore("executor_judge", 0.0, "no artifact — run integration mode first")

    preflight = executor_preflight(case, artifact)
    if preflight.skip_judge:
        return EvalScore(
            "executor_judge",
            0.0,
            f"fatal preflight — judge skipped: {'; '.join(preflight.fatals[:3])}",
        )

    if use_cache and artifact.run_id:
        cached = load_cached_verdict(
            artifact.run_id,
            case.case_id,
            "executor",
            artifact_hash=artifact.artifact_hash,
            judge_version=judge_version,
        )
        if cached:
            norm = cached.normalized(EXECUTOR_CRITERIA_WEIGHTS)
            return EvalScore(
                "executor_judge",
                norm,
                format_judge_comment(cached, cached=True),
            )

    system = load_prompt("executor_judge_system.md")
    user = build_executor_judge_prompt(case, artifact, preflight)
    try:
        verdict = invoke_judge(system, user, llm=llm or get_judge_llm())
    except Exception as e:  # noqa: BLE001
        return EvalScore("executor_judge", 0.0, f"judge error: {e}")

    if artifact.run_id:
        save_cached_verdict(
            artifact.run_id,
            case.case_id,
            "executor",
            verdict,
            artifact_hash=artifact.artifact_hash,
            judge_version=judge_version,
        )

    norm = verdict.normalized(EXECUTOR_CRITERIA_WEIGHTS)
    return EvalScore("executor_judge", norm, format_judge_comment(verdict))


def executor_judge_from_artifact(
    case: "EvalCase",
    artifact: "PipelineArtifact",
    *,
    llm=None,
    use_cache: bool = True,
    judge_version: str = JUDGE_VERSION,
) -> EvalScore:
    """Score executor directly from artifact."""
    preflight = executor_preflight(case, artifact)
    if preflight.skip_judge:
        return EvalScore(
            "executor_judge",
            0.0,
            f"fatal preflight — judge skipped: {'; '.join(preflight.fatals[:3])}",
        )

    if use_cache and artifact.run_id:
        cached = load_cached_verdict(
            artifact.run_id,
            case.case_id,
            "executor",
            artifact_hash=artifact.artifact_hash,
            judge_version=judge_version,
        )
        if cached:
            return EvalScore(
                "executor_judge",
                cached.normalized(EXECUTOR_CRITERIA_WEIGHTS),
                format_judge_comment(cached, cached=True),
            )

    system = load_prompt("executor_judge_system.md")
    user = build_executor_judge_prompt(case, artifact, preflight)
    try:
        verdict = invoke_judge(system, user, llm=llm or get_judge_llm())
    except Exception as e:  # noqa: BLE001
        return EvalScore("executor_judge", 0.0, f"judge error: {e}")

    if artifact.run_id:
        save_cached_verdict(
            artifact.run_id,
            case.case_id,
            "executor",
            verdict,
            artifact_hash=artifact.artifact_hash,
            judge_version=judge_version,
        )

    return EvalScore(
        "executor_judge",
        verdict.normalized(EXECUTOR_CRITERIA_WEIGHTS),
        format_judge_comment(verdict),
    )
