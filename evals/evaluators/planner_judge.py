"""Planner LLM-as-Judge evaluator."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from evals.evaluators.base import EvalScore
from evals.evaluators.llm_judge_base import (
    JUDGE_VERSION,
    PLANNER_CRITERIA_WEIGHTS,
    format_file_contexts,
    format_judge_comment,
    format_warnings,
    get_judge_llm,
    invoke_judge,
    load_cached_verdict,
    load_prompt,
    save_cached_verdict,
)
from evals.evaluators.preflight import planner_preflight

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


def build_planner_judge_prompt(case: "EvalCase", artifact: "PipelineArtifact", preflight) -> str:
    """Build user prompt for planner judge."""
    golden = artifact.golden or {}
    research = artifact.research_output or {}
    summary = research.get("research_summary") or "(none)"
    plan_json = json.dumps(artifact.plan, indent=2)[:8000] if artifact.plan else "(none)"
    return f"""## Case
- case_id: {case.case_id}
- vuln_class: {golden.get('vuln_class', case.vuln_class)} / CWE: {golden.get('cwe', case.cwe)}
- ticket: {json.dumps(artifact.ticket, indent=2)[:4000]}
- pass_criteria: {json.dumps(golden.get('pass_criteria', case.pass_criteria))}

## Research
{summary}
affected_files: {json.dumps([f.get('path') if isinstance(f, dict) else f for f in research.get('affected_files', [])])}

## Planner Output
{plan_json}

## Deterministic Preflight
score: {preflight.score}
fatals: {json.dumps(preflight.fatals)}
warnings: {json.dumps(preflight.warnings)}

## Preflight Warnings (resolver may be wrong — evaluate plan logic independently)
{format_warnings(preflight.warnings)}

## Repo Context (before-edit excerpts)
{format_file_contexts(artifact.before_context)}

## Task
Score the PLANNER stage. Evaluate whether the execution plan correctly addresses the ROOT CAUSE of the security issue.
Return JSON:
{{
  "score": 0-100,
  "passed": bool,
  "criteria": {{
    "root_cause_analysis": {{"score": 0-100, "reason": ""}},
    "technical_correctness": {{"score": 0-100, "reason": ""}},
    "file_selection": {{"score": 0-100, "reason": ""}},
    "semantic_targeting": {{"score": 0-100, "reason": ""}},
    "instruction_quality": {{"score": 0-100, "reason": ""}},
    "executability": {{"score": 0-100, "reason": ""}},
    "no_hallucination": {{"score": 0-100, "reason": ""}}
  }},
  "hard_failures": [],
  "soft_failures": [],
  "reasoning": ""
}}"""


def planner_judge(
    case: "EvalCase",
    pred: "Prediction",
    *,
    llm=None,
    use_cache: bool = True,
    judge_version: str = JUDGE_VERSION,
) -> EvalScore:
    """Score planner via LLM judge (requires PipelineArtifact)."""
    if "plan" not in case.eval_scope:
        return EvalScore("planner_judge", 0.0, "not in scope", applicable=False)

    artifact = _artifact_from_pred(pred)
    if artifact is None:
        return EvalScore("planner_judge", 0.0, "no artifact — run integration mode first")

    preflight = planner_preflight(case, artifact)
    if preflight.skip_judge:
        return EvalScore(
            "planner_judge",
            0.0,
            f"fatal preflight — judge skipped: {'; '.join(preflight.fatals[:3])}",
        )

    if use_cache and artifact.run_id:
        cached = load_cached_verdict(
            artifact.run_id,
            case.case_id,
            "planner",
            artifact_hash=artifact.artifact_hash,
            judge_version=judge_version,
        )
        if cached:
            norm = cached.normalized(PLANNER_CRITERIA_WEIGHTS)
            return EvalScore(
                "planner_judge",
                norm,
                format_judge_comment(cached, cached=True),
            )

    system = load_prompt("planner_judge_system.md")
    user = build_planner_judge_prompt(case, artifact, preflight)
    try:
        verdict = invoke_judge(system, user, llm=llm or get_judge_llm())
    except Exception as e:  # noqa: BLE001
        return EvalScore("planner_judge", 0.0, f"judge error: {e}")

    if artifact.run_id:
        save_cached_verdict(
            artifact.run_id,
            case.case_id,
            "planner",
            verdict,
            artifact_hash=artifact.artifact_hash,
            judge_version=judge_version,
        )

    norm = verdict.normalized(PLANNER_CRITERIA_WEIGHTS)
    comment = format_judge_comment(verdict)
    if preflight.warnings:
        comment += f"\n\nPreflight warnings: {len(preflight.warnings)}"
    return EvalScore("planner_judge", norm, comment)


def planner_judge_from_artifact(
    case: "EvalCase",
    artifact: "PipelineArtifact",
    *,
    llm=None,
    use_cache: bool = True,
    judge_version: str = JUDGE_VERSION,
) -> EvalScore:
    """Score planner directly from artifact (LangSmith / offline judge path)."""
    from evals.harness.prediction import Prediction

    pred = Prediction(case_id=case.case_id, artifact_path="")
    pred._artifact = artifact  # type: ignore[attr-defined]
    preflight = planner_preflight(case, artifact)
    if preflight.skip_judge:
        return EvalScore(
            "planner_judge",
            0.0,
            f"fatal preflight — judge skipped: {'; '.join(preflight.fatals[:3])}",
        )

    if use_cache and artifact.run_id:
        cached = load_cached_verdict(
            artifact.run_id,
            case.case_id,
            "planner",
            artifact_hash=artifact.artifact_hash,
            judge_version=judge_version,
        )
        if cached:
            norm = cached.normalized(PLANNER_CRITERIA_WEIGHTS)
            return EvalScore("planner_judge", norm, format_judge_comment(cached, cached=True))

    system = load_prompt("planner_judge_system.md")
    user = build_planner_judge_prompt(case, artifact, preflight)
    try:
        verdict = invoke_judge(system, user, llm=llm or get_judge_llm())
    except Exception as e:  # noqa: BLE001
        return EvalScore("planner_judge", 0.0, f"judge error: {e}")

    if artifact.run_id:
        save_cached_verdict(
            artifact.run_id,
            case.case_id,
            "planner",
            verdict,
            artifact_hash=artifact.artifact_hash,
            judge_version=judge_version,
        )

    norm = verdict.normalized(PLANNER_CRITERIA_WEIGHTS)
    return EvalScore("planner_judge", norm, format_judge_comment(verdict))
