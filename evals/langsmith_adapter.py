"""LangSmith-compatible targets and evaluators (Milestone 2.3).

Wraps the same harness targets / deterministic evaluators used by run_eval so
langsmith.evaluate() can reuse them without a parallel scoring implementation.

Usage:
  python -m evals.runners.run_langsmith --mode research --case-ids G-01

  # or programmatically:
  from langsmith.evaluation import evaluate
  from evals.langsmith_adapter import (
      configure_langsmith_cases,
      make_langsmith_target,
      PLAN_EVALUATORS,
  )

  configure_langsmith_cases("evals/golden/unit_test")
  evaluate(
      make_langsmith_target("plan"),
      data="IntuitionX-Golden-20-v1",
      evaluators=PLAN_EVALUATORS,
      experiment_prefix="week2-plan-baseline",
      max_concurrency=1,
  )
"""

from __future__ import annotations

import os
import sys
from typing import Any

# Shared run directory for multi-case LangSmith integration/e2e experiments.
_LANGSMITH_RUN_ID: str | None = None


def set_langsmith_run_id(run_id: str) -> None:
    """Pin one evals/runs/ directory for all cases in a LangSmith experiment."""
    global _LANGSMITH_RUN_ID
    _LANGSMITH_RUN_ID = run_id
    os.environ["EVAL_RUN_ID"] = run_id


def get_langsmith_run_id() -> str:
    return _LANGSMITH_RUN_ID or os.environ.get("EVAL_RUN_ID", "")

from evals.evaluators.executor_judge import executor_judge_from_artifact
from evals.evaluators.planner_judge import planner_judge_from_artifact
from evals.evaluators.preflight_scores import executor_preflight_score, planner_preflight_score
from evals.evaluators.scoring import (
    file_identification_f1,
    fix_type_match,
    patch_contains_excludes,
    plan_line_overlap,
    route_correctness,
)
from evals.harness.prediction import Prediction
from evals.harness.targets import TARGETS
from evals.schema.case import EvalCase, ExpectedLineRange, RepoSnapshot, TicketInput

_GOLDEN_CASES: dict[str, EvalCase] | None = None
_CASES_DIR = "evals/golden/unit_test"


def configure_langsmith_cases(cases_dir: str) -> None:
    """Load golden cases for LangSmith targets/evaluators (call before evaluate())."""
    global _GOLDEN_CASES, _CASES_DIR
    from evals.schema.case import load_cases

    _CASES_DIR = cases_dir
    _GOLDEN_CASES = {c.case_id: c for c in load_cases(cases_dir)}


def _golden_cases() -> dict[str, EvalCase]:
    """Golden Cases for this module."""
    if _GOLDEN_CASES is None:
        configure_langsmith_cases(_CASES_DIR)
    assert _GOLDEN_CASES is not None
    return _GOLDEN_CASES


def _example_outputs(example: Any) -> dict[str, Any]:
    """Example Outputs for this module."""
    return getattr(example, "outputs", None) or {}


def _example_metadata(example: Any) -> dict[str, Any]:
    """Example Metadata for this module."""
    return getattr(example, "metadata", None) or {}


def _case_id_from_example(example: Any) -> str:
    """Case Id From Example for this module."""
    meta = _example_metadata(example)
    out = _example_outputs(example)
    return str(meta.get("case_id") or out.get("case_id") or "")


def _case_id_from_inputs(inputs: dict[str, Any]) -> str:
    """Case Id From Inputs for this module."""
    jira = inputs.get("jira_ticket") or {}
    return str(jira.get("issue_id") or "")


def _case_from_langsmith_inputs(inputs: dict[str, Any]) -> EvalCase:
    """Resolve EvalCase for a LangSmith example — prefer local golden JSON."""
    case_id = _case_id_from_inputs(inputs)
    golden = _golden_cases()
    if case_id and case_id in golden:
        return golden[case_id]

    jira = inputs.get("jira_ticket") or {}
    snap = inputs.get("repo_snapshot") or {}
    ticket = TicketInput(
        title=str(jira.get("title") or ""),
        problem_statement=str(jira.get("problem_statement") or ""),
        issue_type=str(jira.get("issue_type") or "Security"),
        status=str(jira.get("status") or "In Progress"),
        priority_raw=str(jira.get("priority_raw") or "High"),
        priority_normalized=str(jira.get("priority_normalized") or "P1"),
        module=str(jira.get("module") or ""),
        labels=list(jira.get("labels") or []),
        repo_links=list(jira.get("repo_links") or []),
        acceptance_criteria=list(jira.get("acceptance_criteria") or []),
        vulnerability_category=str(jira.get("vulnerability_category") or ""),
    )
    repo = RepoSnapshot.model_validate(snap) if snap else RepoSnapshot()
    return EvalCase(
        case_id=case_id or "unknown",
        title=ticket.title or case_id or "unknown",
        repo=repo,
        ticket=ticket,
        expected_route=str(jira.get("expected_route") or "FULL"),
    )


def _case_from_langsmith_example(example: Any, *, scope: str | None = None) -> EvalCase:
    """Case From Langsmith Example for this module."""
    case_id = _case_id_from_example(example)
    golden = _golden_cases()
    if case_id and case_id in golden:
        return golden[case_id]

    out = _example_outputs(example)
    scopes = [scope] if scope else ["routing", "research", "plan", "implement"]
    line_ranges = [
        ExpectedLineRange.model_validate(item)
        for item in (out.get("expected_line_ranges") or [])
    ]
    return EvalCase(
        case_id=case_id,
        title="",
        ticket=TicketInput(title="", problem_statement=""),
        expected_route=str(out.get("expected_route", "FULL")),
        expected_files=list(out.get("expected_files") or []),
        expected_line_ranges=line_ranges,
        expected_fix_type=str(out.get("expected_fix_type") or ""),
        expected_patch_contains=list(out.get("expected_patch_contains") or []),
        expected_patch_excludes=list(out.get("expected_patch_excludes") or []),
        eval_scope=scopes,
    )


def _unwrap_run_outputs(run: Any) -> dict[str, Any]:
    """Unwrap Run Outputs for this module."""
    outputs = getattr(run, "outputs", None) or {}
    if not isinstance(outputs, dict):
        return {}
    if "case_id" in outputs or "route" in outputs or "files" in outputs:
        return outputs
    inner = outputs.get("output")
    if isinstance(inner, dict):
        return inner
    return outputs


def _pred_from_run(run: Any, case_id: str) -> Prediction:
    """Pred From Run for this module."""
    outputs = _unwrap_run_outputs(run)
    return Prediction.model_validate({"case_id": case_id, **outputs})


def _prediction_to_langsmith_output(pred: Prediction) -> dict[str, Any]:
    """Serialize prediction for LangSmith — keep empty fields so the UI is never blank."""
    return pred.model_dump()


def _score_to_langsmith(res) -> dict[str, Any]:
    """Score To Langsmith for this module."""
    return {"key": res.key, "score": res.score, "comment": res.comment}


def _artifact_from_pred(pred: Prediction):
    if not pred.artifact_path:
        return None
    from evals.artifacts.pipeline_result import PipelineArtifact

    return PipelineArtifact.model_validate_json(
        open(pred.artifact_path, encoding="utf-8").read()
    )


def planner_judge_langsmith(run: Any, example: Any) -> dict[str, Any]:
    """Planner LLM judge LangSmith evaluator."""
    case = _case_from_langsmith_example(example, scope="plan")
    pred = _pred_from_run(run, case.case_id)
    artifact = _artifact_from_pred(pred)
    if artifact is None:
        return {"key": "planner_judge", "score": 0.0, "comment": "no artifact"}
    res = planner_judge_from_artifact(case, artifact)
    return _score_to_langsmith(res)


def executor_judge_langsmith(run: Any, example: Any) -> dict[str, Any]:
    """Executor LLM judge LangSmith evaluator."""
    case = _case_from_langsmith_example(example, scope="implement")
    pred = _pred_from_run(run, case.case_id)
    artifact = _artifact_from_pred(pred)
    if artifact is None:
        return {"key": "executor_judge", "score": 0.0, "comment": "no artifact"}
    res = executor_judge_from_artifact(case, artifact)
    return _score_to_langsmith(res)


def make_langsmith_target(mode: str):
    """Build a LangSmith target that always returns a Prediction-shaped dict."""
    fn = TARGETS[mode]

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        """Target for this module."""
        case_id = _case_id_from_inputs(inputs) or "unknown"
        print(f"  LangSmith target [{mode}] starting {case_id}...", flush=True)
        pred = Prediction(case_id=case_id)
        try:
            case = _case_from_langsmith_inputs(inputs)
            if mode in ("implement", "integration", "e2e"):
                run_id = get_langsmith_run_id()
                pred = fn(
                    case,
                    dry_run=(mode == "implement"),
                    save_artifact=mode in ("integration", "e2e", "implement"),
                    run_id=run_id,
                )
            else:
                pred = fn(case)
        except Exception as e:  # noqa: BLE001
            pred.error = str(e)
            pred.stage_failed = mode
            print(f"  LangSmith target [{mode}] {case_id} ERROR: {e}", file=sys.stderr, flush=True)
        else:
            err = pred.error or ""
            if err:
                print(
                    f"  LangSmith target [{mode}] {case_id} done (stage_failed={pred.stage_failed})",
                    flush=True,
                )
            else:
                print(f"  LangSmith target [{mode}] {case_id} done", flush=True)
        return _prediction_to_langsmith_output(pred)

    target.__name__ = f"{mode}_langsmith_target"
    return target


research_langsmith_target = make_langsmith_target("research")
routing_langsmith_target = make_langsmith_target("routing")
plan_langsmith_target = make_langsmith_target("plan")
implement_langsmith_target = make_langsmith_target("implement")
integration_langsmith_target = make_langsmith_target("integration")
e2e_langsmith_target = make_langsmith_target("e2e")


def file_f1_langsmith(run: Any, example: Any) -> dict[str, Any]:
    """File F1 Langsmith for this module."""
    case = _case_from_langsmith_example(example, scope="research")
    pred = _pred_from_run(run, case.case_id)
    return _score_to_langsmith(file_identification_f1(case, pred))


def route_correct_langsmith(run: Any, example: Any) -> dict[str, Any]:
    """Route Correct Langsmith for this module."""
    case = _case_from_langsmith_example(example, scope="routing")
    pred = _pred_from_run(run, case.case_id)
    return _score_to_langsmith(route_correctness(case, pred))


def fix_type_match_langsmith(run: Any, example: Any) -> dict[str, Any]:
    """Fix Type Match Langsmith for this module."""
    case = _case_from_langsmith_example(example, scope="plan")
    pred = _pred_from_run(run, case.case_id)
    return _score_to_langsmith(fix_type_match(case, pred))


def plan_line_overlap_langsmith(run: Any, example: Any) -> dict[str, Any]:
    """Plan Line Overlap Langsmith for this module."""
    case = _case_from_langsmith_example(example, scope="plan")
    pred = _pred_from_run(run, case.case_id)
    return _score_to_langsmith(plan_line_overlap(case, pred))


def patch_contains_langsmith(run: Any, example: Any) -> dict[str, Any]:
    """Patch Contains Langsmith for this module."""
    case = _case_from_langsmith_example(example, scope="implement")
    pred = _pred_from_run(run, case.case_id)
    return _score_to_langsmith(patch_contains_excludes(case, pred))


def planner_preflight_langsmith(run: Any, example: Any) -> dict[str, Any]:
    """Planner preflight LangSmith evaluator."""
    case = _case_from_langsmith_example(example, scope="plan")
    pred = _pred_from_run(run, case.case_id)
    return _score_to_langsmith(planner_preflight_score(case, pred))


def executor_preflight_langsmith(run: Any, example: Any) -> dict[str, Any]:
    """Executor preflight LangSmith evaluator."""
    case = _case_from_langsmith_example(example, scope="implement")
    pred = _pred_from_run(run, case.case_id)
    return _score_to_langsmith(executor_preflight_score(case, pred))


RESEARCH_EVALUATORS = [file_f1_langsmith]
ROUTING_EVALUATORS = [route_correct_langsmith]
PLAN_EVALUATORS = [planner_judge_langsmith]
IMPLEMENT_EVALUATORS = [executor_judge_langsmith]
INTEGRATION_EVALUATORS = [
    route_correct_langsmith,
    file_f1_langsmith,
    planner_preflight_langsmith,
    planner_judge_langsmith,
    executor_preflight_langsmith,
    executor_judge_langsmith,
]
E2E_EVALUATORS = INTEGRATION_EVALUATORS

LEGACY_PLAN_EVALUATORS = [fix_type_match_langsmith, plan_line_overlap_langsmith]
LEGACY_IMPLEMENT_EVALUATORS = [patch_contains_langsmith]
LEGACY_E2E_EVALUATORS = [
    route_correct_langsmith,
    file_f1_langsmith,
    fix_type_match_langsmith,
    plan_line_overlap_langsmith,
    patch_contains_langsmith,
]

MODE_TARGETS = {
    "routing": routing_langsmith_target,
    "research": research_langsmith_target,
    "plan": plan_langsmith_target,
    "implement": implement_langsmith_target,
    "integration": make_langsmith_target("integration"),
    "e2e": e2e_langsmith_target,
}

MODE_EVALUATORS = {
    "routing": ROUTING_EVALUATORS,
    "research": RESEARCH_EVALUATORS,
    "plan": PLAN_EVALUATORS,
    "implement": IMPLEMENT_EVALUATORS,
    "integration": INTEGRATION_EVALUATORS,
    "e2e": E2E_EVALUATORS,
}

LEGACY_MODE_EVALUATORS = {
    "plan": LEGACY_PLAN_EVALUATORS,
    "implement": LEGACY_IMPLEMENT_EVALUATORS,
    "e2e": LEGACY_E2E_EVALUATORS,
}
