"""Build PipelineArtifact from live pipeline stage outputs."""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

from evals.artifacts.pipeline_result import (
    FileContext,
    PipelineArtifact,
    build_golden_snapshot,
    capture_file_context,
    now_iso,
)
from evals.harness.ticket_builder import build_jira_ticket
from evals.schema.case import EvalCase


def _repo_git_diff(repo_path: str) -> str:
    """Full working-tree diff for eval artifact."""
    try:
        result = subprocess.run(
            ["git", "diff"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            check=False,
        )
        return (result.stdout or "").strip()
    except OSError:
        return ""


def _step_dict(step: Any) -> dict[str, Any]:
    if hasattr(step, "model_dump"):
        return step.model_dump()
    if isinstance(step, dict):
        return step
    return {}


def _edit_center_line(step: dict[str, Any]) -> int:
    """Best line hint for context capture."""
    approx = int(step.get("approximate_line") or 0)
    if approx > 0:
        return approx
    read_start = int(step.get("read_start_line") or 0)
    if read_start > 0:
        return read_start
    return 1


def capture_edit_contexts(
    plan: Any,
    repo_path: str,
    *,
    phase: str,
) -> list[FileContext]:
    """Capture ±50 line context for each EDIT step (before or after executor)."""
    del phase
    contexts: list[FileContext] = []
    seen: set[tuple[str, str | None]] = set()
    for raw in getattr(plan, "execution_steps", []) or []:
        step = _step_dict(raw)
        if (step.get("action") or "").upper() != "EDIT":
            continue
        file_path = step.get("file") or ""
        if not file_path:
            continue
        target_name = step.get("target_name") or step.get("target") or None
        key = (file_path, target_name)
        if key in seen:
            continue
        seen.add(key)
        center = _edit_center_line(step)
        contexts.append(
            capture_file_context(
                repo_path,
                file_path,
                center_line=center,
                function=target_name,
            )
        )
    return contexts


def build_plan_validation(plan: Any, repo_path: str, report: Any | None) -> dict[str, Any]:
    """Run planner validation and collect fatal/warning signals."""
    out: dict[str, Any] = {"fatals": [], "warnings": [], "flags": [], "edit_outcomes": []}
    if plan is None:
        out["fatals"].append("no plan produced")
        return out

    flags = list(getattr(plan, "flags", []) or [])
    out["flags"] = flags

    try:
        from src.core.plan_validation import validate_execution_plan, validate_instruction_edits
        from src.core.schemas import ExecutionPlan, ResearchReport

        ep = plan if isinstance(plan, ExecutionPlan) else ExecutionPlan.model_validate(plan)
        rr = None
        if report is not None:
            rr = report if isinstance(report, ResearchReport) else None
        soft_flags = validate_execution_plan(ep, repo_path, research=rr)
        for f in soft_flags:
            if f not in out["flags"]:
                out["flags"].append(f)
        hard, _soft = validate_instruction_edits(ep, repo_path)
        for h in hard:
            out["fatals"].append(h)
    except Exception as e:  # noqa: BLE001
        out["warnings"].append(f"validate_execution_plan: {e}")

    for raw in getattr(plan, "execution_steps", []) or []:
        step = _step_dict(raw)
        file_path = step.get("file") or ""
        if file_path:
            full = Path(repo_path) / file_path
            if not full.is_file() and not getattr(plan, "create_files", False):
                if (step.get("action") or "").upper() == "EDIT":
                    out["fatals"].append(f"file does not exist: {file_path}")

    edit_steps = [
        _step_dict(s)
        for s in getattr(plan, "execution_steps", []) or []
        if (_step_dict(s).get("action") or "").upper() == "EDIT"
    ]
    if not edit_steps and getattr(plan, "files_to_change", None):
        out["warnings"].append("files_to_change set but no EDIT steps")

    for step in edit_steps:
        if not (step.get("instruction") or "").strip():
            out["fatals"].append(f"EDIT step {step.get('step_id')}: missing instruction")
        if not (step.get("intent_marker") or "").strip():
            out["fatals"].append(f"EDIT step {step.get('step_id')}: missing intent_marker")
        if not step.get("target_type"):
            out["fatals"].append(f"EDIT step {step.get('step_id')}: missing target_type")
        elif step.get("target_type") != "file" and not (step.get("target_name") or "").strip():
            out["fatals"].append(
                f"EDIT step {step.get('step_id')}: missing target_name for {step.get('target_type')}"
            )
        if step.get("old_string") or step.get("new_string"):
            out["fatals"].append(
                f"EDIT step {step.get('step_id')}: legacy patch fields deprecated"
            )

    return out


def collect_edit_outcomes(step_results: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """Extract edit_outcome telemetry from executor step results."""
    outcomes: list[dict[str, Any]] = []
    for sr in step_results or []:
        if (sr.get("action") or "").upper() != "EDIT":
            continue
        outcome = sr.get("edit_outcome")
        if outcome:
            outcomes.append(dict(outcome))
    return outcomes


def edit_outcomes_to_executor_validations(
    outcomes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Map edit_outcome telemetry to executor_validations for preflight/judge prompts."""
    validations: list[dict[str, Any]] = []
    for outcome in outcomes:
        passed = bool(outcome.get("passed", True))
        reason_parts: list[str] = []
        if outcome.get("noop_diff"):
            reason_parts.append("noop diff")
        if outcome.get("structural_fail"):
            reason_parts.append("structural failure")
        fail_reason = outcome.get("validation_fail_reason")
        if fail_reason:
            reason_parts.append(str(fail_reason))
        validations.append(
            {
                "step_id": outcome.get("step_id"),
                "file": outcome.get("file"),
                "passed": passed,
                "confidence": 1.0 if passed else 0.0,
                "reason": "; ".join(reason_parts) or ("ok" if passed else "validation failed"),
                "noop_diff": bool(outcome.get("noop_diff")),
                "structural_fail": bool(outcome.get("structural_fail")),
                "retry_count": int(outcome.get("retry_count") or 0),
                "missing_requirements": list(outcome.get("missing_requirements") or []),
            }
        )
    return validations


def research_to_dict(report: Any | None) -> dict[str, Any] | None:
    """Serialize research report for artifact."""
    if report is None:
        return None
    if hasattr(report, "model_dump"):
        return report.model_dump()
    return dict(report)


def plan_to_dict(plan: Any | None) -> dict[str, Any] | None:
    if plan is None:
        return None
    if hasattr(plan, "model_dump"):
        return plan.model_dump()
    return dict(plan)


def execution_to_dict(result: Any | None) -> dict[str, Any] | None:
    if result is None:
        return None
    if hasattr(result, "model_dump"):
        return result.model_dump()
    return dict(result)


def build_pipeline_artifact(
    case: EvalCase,
    *,
    run_id: str,
    repo_path: str,
    route: str | None = None,
    report: Any | None = None,
    plan: Any | None = None,
    execution: Any | None = None,
    before_context: list[FileContext] | None = None,
    after_context: list[FileContext] | None = None,
    git_diff: str = "",
    error: str | None = None,
    stage_failed: str | None = None,
    token_usage: dict[str, Any] | None = None,
    dry_run: bool = False,
) -> PipelineArtifact:
    """Assemble a PipelineArtifact from pipeline stage outputs."""
    ticket = build_jira_ticket(case)
    plan_validation = build_plan_validation(plan, repo_path, report) if plan else {"fatals": [], "warnings": []}

    changed_files: list[str] = []
    step_results: list[dict[str, Any]] = []
    if execution is not None:
        changed_files = list(getattr(execution, "changed_files", []) or [])
        step_results = list(getattr(execution, "step_results", []) or [])

    edit_outcomes = collect_edit_outcomes(step_results)
    plan_validation["edit_outcomes"] = edit_outcomes
    executor_validations = edit_outcomes_to_executor_validations(edit_outcomes)

    if not git_diff and repo_path:
        git_diff = _repo_git_diff(repo_path)

    return PipelineArtifact(
        case_id=case.case_id,
        run_id=run_id,
        captured_at=now_iso(),
        ticket=dict(ticket),
        repo={
            "slug": case.repo.slug,
            "url": case.repo.url,
            "commit_sha": case.repo.commit_sha,
            "local_path": repo_path,
        },
        golden=build_golden_snapshot(case),
        route=route,
        research_output=research_to_dict(report),
        plan=plan_to_dict(plan),
        plan_validation=plan_validation,
        execution=execution_to_dict(execution),
        executor_validations=executor_validations,
        git_diff=git_diff,
        changed_files=changed_files,
        step_results=step_results,
        before_context=before_context or [],
        after_context=after_context or [],
        error=error,
        stage_failed=stage_failed,
        token_usage=token_usage or {},
        dry_run=dry_run,
    ).finalize()
