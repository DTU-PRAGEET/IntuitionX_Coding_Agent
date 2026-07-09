"""Live targets — run real pipeline stages for one case and return a Prediction.

All `src` imports are lazy so the offline scoring path (and schema validation)
never pays the cost of loading LangChain/Cursor. Each target degrades to an
error-tagged Prediction instead of raising, so one bad case never aborts a run.

Milestone 2.3 unit modes:
  routing   — orchestrator only
  research  — research agent only
  plan      — routing + research + planner
  implement — routing + research + planner + executor (dry_run by default)
  e2e       — full pipeline (alias of implement with dry_run=False option)
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from evals.harness.prediction import Prediction
from evals.harness.repo import resolve_repo_path, resolve_repo_paths
from evals.harness.ticket_builder import build_jira_ticket

if TYPE_CHECKING:
    from evals.schema.case import EvalCase


_FIX_TYPE_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    (
        "parameterize_sql",
        (
            "preparedstatement",
            "parameterize",
            "parameterised",
            "parameterized",
            "setstring",
            "bindparam",
            "bind parameter",
            "placeholder",
        ),
    ),
    ("auth_check", ("authorization", "authorisation", "ownership", "forbidden", "access denied")),
    ("xss_encoding", ("autoescape", "escapehtml", "html.escape", "dompurify", "innerhtml")),
    ("secrets_removal", ("bcrypt", "argon2", "scrypt", "environment variable", "env var")),
    ("dependency_upgrade", ("upgrade dependency", "bump version", "cve-", "npm audit")),
    ("sanitize_input", ("sanitize", "sanitise", "validate input", "allowlist")),
]

_FIX_TYPE_FIELD_NAMES = (
    "fix_type",
    "target",
    "reason",
    "change",
    "instruction",
    "new_string",
    "old_string",
)


def _plan_text_blob(plan: Any) -> str:
    """Plan Text Blob for this module."""
    parts: list[str] = []
    for field in getattr(plan, "implementation_constraints", []) or []:
        parts.append(str(field))
    for step in getattr(plan, "execution_steps", []) or []:
        for field in _FIX_TYPE_FIELD_NAMES:
            val = getattr(step, field, "") or ""
            if val:
                parts.append(str(val))
    return " ".join(parts).lower()


def _infer_fix_type(plan: Any) -> str:
    """Infer Fix Type for this module."""
    primary = getattr(plan, "primary_fix_type", None)
    if primary:
        return str(primary)

    blob = _plan_text_blob(plan)
    for fix_type, keywords in _FIX_TYPE_KEYWORDS:
        if any(keyword in blob for keyword in keywords):
            return fix_type

    for step in getattr(plan, "execution_steps", []) or []:
        for field in ("fix_type", "target", "reason", "change"):
            val = (getattr(step, field, "") or "").strip().lower().replace("-", "_").replace(" ", "_")
            if not val:
                continue
            if val in {ft for ft, _ in _FIX_TYPE_KEYWORDS}:
                return val
            first = val.split("_")[0] if "_" in val else val.split()[0]
            if first in {ft for ft, _ in _FIX_TYPE_KEYWORDS}:
                return first
    return ""


def _infer_range_from_old_string(
    repo_path: str,
    file_path: str,
    old_string: str,
    *,
    context_after: int = 5,
) -> tuple[int, int] | None:
    """Infer Range From Old String for this module."""
    if not old_string.strip():
        return None
    full = Path(repo_path) / file_path
    if not full.is_file():
        return None
    try:
        content = full.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None

    normalized_content = content.replace("\r\n", "\n")
    normalized_old = old_string.replace("\r\n", "\n")
    idx = normalized_content.find(normalized_old)
    if idx < 0:
        return None

    start_line = normalized_content[:idx].count("\n") + 1
    end_line = start_line + max(0, normalized_old.count("\n"))
    total_lines = normalized_content.count("\n") + 1
    end_line = min(end_line + context_after, total_lines)
    return start_line, end_line



def _plan_line_ranges(
    plan: Any,
    *,
    report: Any | None = None,
    repo_path: str | None = None,
) -> list[dict[str, Any]]:
    """Extract planner line targets for eval scoring.

  Priority per step/file:
    1. explicit read_start_line / read_end_line on the step
    2. approximate_line hint on EDIT steps
    3. legacy EDIT old_string position on disk (deprecated)
    """
    out: list[dict[str, Any]] = []
    seen: set[tuple[str, int, int]] = set()

    def _append(file_path: str, start: int, end: int) -> None:
        """Append for this module."""
        if start <= 0 or end <= 0:
            return
        key = (file_path, start, end)
        if key in seen:
            return
        seen.add(key)
        out.append({"file": file_path, "start": start, "end": end})

    for step in getattr(plan, "execution_steps", []) or []:
        file_path = getattr(step, "file", "") or ""
        if not file_path:
            continue

        start = int(getattr(step, "read_start_line", 0) or 0)
        end = int(getattr(step, "read_end_line", 0) or 0)

        if start <= 0 or end <= 0:
            action = getattr(step, "action", "") or ""
            approx = int(getattr(step, "approximate_line", 0) or 0)
            if action == "EDIT" and approx > 0:
                start, end = approx, approx + 5
            elif action == "EDIT" and repo_path:
                old_string = getattr(step, "old_string", "") or ""
                inferred = _infer_range_from_old_string(repo_path, file_path, old_string)
                if inferred:
                    start, end = inferred

        if start > 0 and end > 0:
            _append(file_path, start, end)

    return out


def _fill_plan_fields(
    pred: Prediction,
    plan: Any,
    *,
    report: Any | None = None,
    repo_path: str | None = None,
) -> None:
    """Fill Plan Fields for this module."""
    pred.plan_files = list(getattr(plan, "files_to_change", []) or [])
    pred.plan_fix_type = _infer_fix_type(plan)
    pred.plan_line_ranges = _plan_line_ranges(plan, report=report, repo_path=repo_path)


def _is_micro_expected_route(case: "EvalCase") -> bool:
    """True when the golden answer key expects MICRO (incl. legacy SKIP_RESEARCH)."""
    route = (case.expected_route or "").strip().upper()
    return route in ("MICRO", "SKIP_RESEARCH")


def _routing_repo_context(case: "EvalCase") -> tuple[str | None, dict | None]:
    """Resolve local checkout for MICRO routing evals (mirrors orchestrator_node)."""
    if not _is_micro_expected_route(case):
        return None, None
    try:
        repo_path = resolve_repo_path(case)
    except Exception:  # noqa: BLE001
        return None, None
    if not repo_path:
        return None, None
    try:
        from src.agents.research.tools.repo_profile import profile_repository

        profile = profile_repository(repo_path)
    except Exception:  # noqa: BLE001
        profile = None
    return repo_path, profile


def _resolve_route(case: "EvalCase", pred: Prediction, *, llm=None) -> bool:
    """Resolve routing into pred.route. Returns False when pipeline should stop (EXIT/error)."""
    ticket = build_jira_ticket(case)
    repo_path, repo_profile = _routing_repo_context(case)
    try:
        from src.pipeline.runner import resolve_routing

        decision = resolve_routing(
            ticket,
            llm=llm,
            repo_path=repo_path,
            repo_profile=repo_profile,
        )
        pred.route = decision.routing_route
        return decision.routing_route != "EXIT"
    except Exception as e:  # noqa: BLE001
        pred.error = str(e)
        pred.stage_failed = "orchestrator"
        return False


def _run_research(case: "EvalCase", pred: Prediction, *, llm=None) -> Any | None:
    """Run research; populate pred.files. Returns ResearchReport or None on failure."""
    ticket = build_jira_ticket(case)
    try:
        repo_paths = resolve_repo_paths(case)
    except Exception as e:  # noqa: BLE001
        pred.error = f"repo: {e}"
        pred.stage_failed = "research"
        return None

    try:
        from src.agents.research.deep_runner import run_research
        from src.agents.research.memory import InMemoryResearchMemory
        from src.agents.research.tools.file_discovery import extract_anchors
        from src.agents.research.tools.repo_profile import profile_repository

        all_files: list[str] = []
        last_report = None
        for repo_path in repo_paths:
            profile = profile_repository(repo_path)
            anchors = extract_anchors(ticket, "")
            report = run_research(
                ticket=ticket,
                repo_path=repo_path,
                repo_profile=profile,
                anchors=anchors,
                memory=InMemoryResearchMemory(),
                llm=llm,
            )
            last_report = report
            all_files.extend(f.path for f in report.affected_files)
        pred.files = list(dict.fromkeys(all_files))
        if last_report is not None:
            pred.research_summary = last_report.research_summary
        pred._repo_path = repo_paths[0]  # type: ignore[attr-defined]
        return last_report
    except Exception as e:  # noqa: BLE001
        pred.error = str(e)
        pred.stage_failed = "research"
        return None


def run_routing_target(case: "EvalCase", *, llm=None) -> Prediction:
    """Orchestrator routing only — cheap, no repo tools."""
    pred = Prediction(case_id=case.case_id)
    ticket = build_jira_ticket(case)
    repo_path, repo_profile = _routing_repo_context(case)
    try:
        from src.pipeline.runner import resolve_routing

        decision = resolve_routing(
            ticket,
            llm=llm,
            repo_path=repo_path,
            repo_profile=repo_profile,
        )
        pred.route = decision.routing_route
    except Exception as e:  # noqa: BLE001
        pred.error = str(e)
        pred.stage_failed = "orchestrator"
    return pred


def run_research_target(case: "EvalCase", *, llm=None) -> Prediction:
    """Research agent only — measures file identification (Milestone 2.3)."""
    pred = Prediction(case_id=case.case_id)
    _run_research(case, pred, llm=llm)
    return pred


def run_plan_target(case: "EvalCase", *, llm=None) -> Prediction:
    """Routing + Research + Planner — measures fix type and line targeting."""
    pred = Prediction(case_id=case.case_id)
    if not _resolve_route(case, pred, llm=llm):
        return pred

    report = _run_research(case, pred, llm=llm)
    if report is None:
        return pred

    repo_path = getattr(pred, "_repo_path", None)
    ticket = build_jira_ticket(case)
    try:
        from src.agents.planner.agent import run_planner

        plan = run_planner(
            report, repo_path, routing_route=pred.route or "FULL", ticket=ticket,
        )
        _fill_plan_fields(pred, plan, report=report, repo_path=repo_path)
    except Exception as e:  # noqa: BLE001
        pred.error = str(e)
        pred.stage_failed = "planner"
    return pred


def run_implement_target(
    case: "EvalCase",
    *,
    llm=None,
    dry_run: bool = True,
    run_id: str = "",
    save_artifact: bool = True,
) -> Prediction:
    """Routing + Research + Planner + Executor — measures patch quality."""
    return run_integration_target(
        case,
        llm=llm,
        dry_run=dry_run,
        run_id=run_id,
        save_artifact=save_artifact,
    )


def run_integration_target(
    case: "EvalCase",
    *,
    llm=None,
    dry_run: bool = True,
    run_id: str = "",
    save_artifact: bool = True,
) -> Prediction:
    """Full pipeline with optional PipelineArtifact capture to evals/runs/{run_id}/."""
    from evals.harness.artifact_capture import (
        build_pipeline_artifact,
        capture_edit_contexts,
    )

    pred = Prediction(case_id=case.case_id)
    plan = None
    result = None
    report = None
    repo_path: str | None = None
    before_context = []
    after_context = []
    artifact_run_id = run_id or os.environ.get("EVAL_RUN_ID", "")

    if save_artifact and not artifact_run_id:
        from evals.artifacts.pipeline_result import allocate_run_dir

        artifact_run_id, _ = allocate_run_dir()

    if not _resolve_route(case, pred, llm=llm):
        if save_artifact and artifact_run_id:
            from evals.artifacts.pipeline_result import save_artifact as _save

            art = build_pipeline_artifact(
                case,
                run_id=artifact_run_id,
                repo_path=repo_path or "",
                route=pred.route,
                error=pred.error,
                stage_failed=pred.stage_failed,
                dry_run=dry_run,
            )
            path = _save(art)
            pred.artifact_path = str(path)
        return pred

    report = _run_research(case, pred, llm=llm)
    if report is None:
        if save_artifact and artifact_run_id:
            from evals.artifacts.pipeline_result import save_artifact as _save

            art = build_pipeline_artifact(
                case,
                run_id=artifact_run_id,
                repo_path=repo_path or "",
                route=pred.route,
                report=report,
                error=pred.error,
                stage_failed=pred.stage_failed,
                dry_run=dry_run,
            )
            path = _save(art)
            pred.artifact_path = str(path)
        return pred

    repo_path = getattr(pred, "_repo_path", None) or ""
    ticket = build_jira_ticket(case)
    try:
        from src.agents.planner.agent import run_planner

        plan = run_planner(
            report, repo_path, routing_route=pred.route or "FULL", ticket=ticket,
        )
        _fill_plan_fields(pred, plan, report=report, repo_path=repo_path)
        before_context = capture_edit_contexts(plan, repo_path, phase="before")
    except Exception as e:  # noqa: BLE001
        pred.error = str(e)
        pred.stage_failed = "planner"
        if save_artifact and artifact_run_id:
            from evals.artifacts.pipeline_result import save_artifact as _save

            art = build_pipeline_artifact(
                case,
                run_id=artifact_run_id,
                repo_path=repo_path,
                route=pred.route,
                report=report,
                plan=plan,
                before_context=before_context,
                error=pred.error,
                stage_failed=pred.stage_failed,
                dry_run=dry_run,
            )
            path = _save(art)
            pred.artifact_path = str(path)
        return pred

    try:
        from src.agents.executor.agent import run_executor

        result = run_executor(
            plan,
            repo_path,
            dry_run=dry_run,
            ticket=ticket,
            llm=llm,
            continue_on_edit_validation_failure=True,
        )
        pred.changed_files = list(getattr(result, "changed_files", []) or [])
        after_context = capture_edit_contexts(plan, repo_path, phase="after")
        pred.patch_text = _repo_git_diff_for_pred(repo_path)
        pred.compilation_status = getattr(result, "compilation_status", "") or ""
        failed_steps = list(getattr(result, "validation_failed_steps", []) or [])
        if failed_steps:
            pred.error = (
                f"Executor edit validation failed for step(s) {failed_steps} "
                "(pipeline continued for judge evaluation)"
            )
    except Exception as e:  # noqa: BLE001
        pred.error = str(e)
        pred.stage_failed = "executor"
        if repo_path and plan is not None:
            after_context = capture_edit_contexts(plan, repo_path, phase="after")

    if save_artifact and artifact_run_id:
        from evals.artifacts.pipeline_result import save_artifact as _save

        art = build_pipeline_artifact(
            case,
            run_id=artifact_run_id,
            repo_path=repo_path,
            route=pred.route,
            report=report,
            plan=plan,
            execution=result,
            before_context=before_context,
            after_context=after_context,
            error=pred.error,
            stage_failed=pred.stage_failed,
            dry_run=dry_run,
        )
        path = _save(art)
        pred.artifact_path = str(path)

    return pred


def _repo_git_diff_for_pred(repo_path: str) -> str:
    """Git diff for Prediction.patch_text."""
    from evals.harness.artifact_capture import _repo_git_diff

    return _repo_git_diff(repo_path)


def run_e2e_target(case: "EvalCase", *, dry_run: bool = False, llm=None, run_id: str = "", save_artifact: bool = True) -> Prediction:
    """Full Research -> Plan -> Execute via the production pipeline body."""
    return run_integration_target(
        case, llm=llm, dry_run=dry_run, run_id=run_id, save_artifact=save_artifact,
    )


TARGETS = {
    "routing": run_routing_target,
    "research": run_research_target,
    "plan": run_plan_target,
    "implement": run_implement_target,
    "integration": run_integration_target,
    "e2e": run_e2e_target,
}

MODE_TO_SCOPE = {
    "routing": "routing",
    "research": "research",
    "plan": "plan",
    "implement": "implement",
    "integration": None,
    "e2e": None,
}
