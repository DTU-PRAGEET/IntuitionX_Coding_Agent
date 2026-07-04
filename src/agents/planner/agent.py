"""Planning Agent — single Cursor call to produce semantic ExecutionPlan."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langsmith import traceable
from pydantic import ValidationError

from src.core.logging import configure_logging, get_logger
from src.core.plan_validation import (
    sync_files_to_change_from_edit_steps,
    validate_execution_plan,
    validate_semantic_edits,
)
from src.core.file_paths import extract_repo_file_paths_from_texts, repair_repo_from_cursor_temp_clone, remap_cursor_path
from src.core.repo_snapshot import git_repo_snapshot, log_repo_snapshot
from src.core.schemas import ExecutionPlan, ResearchReport
from src.core.state import AgentState, PlanOutput
from src.core.token_usage import reset_tracing_agent, set_tracing_agent
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway
from src.integrations.cursor.semantic_plan_runner import mock_semantic_plan, run_semantic_plan

_log = get_logger("planner")

_INVALID_WHERE = frozenset({".", ""})


def _is_valid_step_path(path: str, repo_path: str) -> bool:
    """Is Valid Step Path for this module."""
    if not path or path in _INVALID_WHERE:
        return False
    if path == repo_path or path.rstrip("/") == repo_path.rstrip("/"):
        return False
    candidate = Path(path)
    if candidate.exists() and candidate.is_dir():
        return False
    return True


def _research_file_paths(report: ResearchReport) -> list[str]:
    """Research File Paths for this module."""
    return [f.path for f in report.affected_files if f.path]


def _remap_plan_path(path: str, repo_path: str) -> str:
    """Remap Cursor-style paths (e.g. temp_clone/sqli/app.py) to repo-relative files."""
    return remap_cursor_path(path, repo_path)


def _normalize_plan_paths(plan: ExecutionPlan, report: ResearchReport, repo_path: str) -> ExecutionPlan:
    """Repair common path mistakes: file='.' and empty files_to_change."""
    candidates = [_remap_plan_path(p, repo_path) for p in _research_file_paths(report)]
    candidates = list(dict.fromkeys(c for c in candidates if c))

    if not plan.files_to_change and candidates:
        plan.files_to_change = list(candidates)
    elif plan.files_to_change:
        plan.files_to_change = list(
            dict.fromkeys(_remap_plan_path(f, repo_path) for f in plan.files_to_change)
        )

    default_file = None
    if len(candidates) == 1:
        default_file = candidates[0]
    elif plan.files_to_change:
        default_file = plan.files_to_change[0]

    for step in plan.execution_steps:
        file_path = _remap_plan_path(step.file, repo_path)
        if file_path != step.file:
            step.file = file_path
        if not _is_valid_step_path(file_path, repo_path) and default_file:
            step.file = default_file
    return sync_files_to_change_from_edit_steps(plan)


def _validate_plan_executable(plan: ExecutionPlan, report: ResearchReport, repo_path: str) -> None:
    """Validate Plan Executable for this module."""
    candidates = _research_file_paths(report)
    if candidates and not plan.files_to_change:
        raise ValueError(
            "Planner produced empty files_to_change but research has affected_files: "
            f"{candidates}"
        )
    if not plan.execution_steps:
        raise ValueError("Planner produced empty execution_steps")
    for step in plan.execution_steps:
        file_path = step.file
        if step.action == "RUN_TESTS":
            continue
        if not _is_valid_step_path(file_path, repo_path):
            raise ValueError(
                f"Planner step {step.step_id} has invalid file={file_path!r} "
                f"(must be a file path, not '.' or repo root)"
            )


def load_research_report(path: Path) -> ResearchReport:
    """Load and validate ResearchReport JSON; raise ValidationError on bad input."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Failed to read research report {path}: {e}") from e
    try:
        return ResearchReport.model_validate_json(raw)
    except ValidationError as e:
        raise ValueError(f"Invalid ResearchReport in {path}: {e}") from e


def _validate_plan_files(plan: ExecutionPlan, repo_path: str | None) -> list[str]:
    """Validate Plan Files for this module."""
    flags: list[str] = []
    if not repo_path:
        return flags
    root = Path(repo_path)
    for fp in plan.files_to_change:
        if not (root / fp).exists() and not Path(fp).exists():
            flags.append(f"File not found during validation: {fp}")
    return flags


def _log_affected_files(plan: ExecutionPlan, repo_path: str) -> None:
    """Log Affected Files for this module."""
    root = Path(repo_path)
    for fp in plan.files_to_change:
        full = root / fp
        _log.info(
            "affected_file",
            path=fp,
            exists=full.is_file(),
            sha256=hashlib.sha256(full.read_bytes()).hexdigest() if full.is_file() else "",
        )


def _plan_to_output(plan: ExecutionPlan) -> PlanOutput:
    """Plan To Output for this module."""
    out: PlanOutput = PlanOutput(
        ticket_id=plan.ticket_id,
        root_hypothesis=plan.root_hypothesis,
        files_to_change=plan.files_to_change,
        implementation_constraints=plan.implementation_constraints,
        execution_steps=[s.model_dump() for s in plan.execution_steps],
        write_tests=plan.write_tests,
        create_files=plan.create_files,
        risk_level=plan.risk_level,
        self_healing_notes=plan.self_healing_notes,
        flags=plan.flags,
    )
    if plan.planner_repo_path:
        out["planner_repo_path"] = plan.planner_repo_path  # type: ignore[typeddict-unknown-key]
    if plan.planner_snapshot_head:
        out["planner_snapshot_head"] = plan.planner_snapshot_head  # type: ignore[typeddict-unknown-key]
    return out


@traceable(name="planner_agent")
def run_planner(
    report: ResearchReport,
    repo_path: str,
    *,
    routing_route: str = "FULL",
    previous_attempts: list[dict[str, Any]] | None = None,
    gate_feedback: str | None = None,
    ticket: dict[str, Any] | None = None,
    git_target_branch: str | None = None,
    repo_type: str | None = None,
    gateway: CursorGateway | None = None,
    dry_run: bool = False,
) -> ExecutionPlan:
    """Single Cursor ask to produce semantic ExecutionPlan with deterministic validation."""
    configure_logging()
    repair_repo_from_cursor_temp_clone(repo_path)
    log_repo_snapshot(_log, "start", repo_path, ticket_id=report.ticket_id, routing_route=routing_route)

    tracing_token = set_tracing_agent("planner")

    has_cursor = bool(os.getenv("CURSOR_API_KEY", "")) and not dry_run
    if gateway is None and has_cursor:
        gateway = get_cursor_gateway(Path(repo_path), dry_run=False)

    try:
        try:
            if gateway is not None:
                plan = run_semantic_plan(
                    report,
                    repo_path,
                    gateway=gateway,
                    routing_route=routing_route,
                    previous_attempts=previous_attempts,
                    gate_feedback=gate_feedback,
                    ticket=ticket,
                    git_target_branch=git_target_branch,
                    repo_type=repo_type,
                )
            else:
                plan = mock_semantic_plan(report, repo_path)
                plan.flags.append("planner_no_cursor_api_key")

            plan = _normalize_plan_paths(plan, report, repo_path)
            _validate_plan_executable(plan, report, repo_path)

            snap = git_repo_snapshot(repo_path)
            plan.planner_repo_path = snap["repo_path"]
            plan.planner_snapshot_head = snap["head"]
            _log_affected_files(plan, repo_path)
            _log.info(
                "execution_plan_final",
                ticket_id=plan.ticket_id,
                plan_json=plan.model_dump_json(),
            )
        except ValidationError as e:
            _log.error("plan_validation_failed", error=str(e))
            raise ValueError(f"Planner output failed schema validation: {e}") from e
        except Exception as e:
            _log.error("planner_failed", error=str(e))
            raise RuntimeError(
                f"Planner failed for ticket={report.ticket_id!r} repo={repo_path!r}: {e}"
            ) from e

        hard_errors, soft_flags = validate_semantic_edits(plan, repo_path)
        if hard_errors:
            _log.error("semantic_edit_validation_failed", errors=hard_errors)
            raise ValueError(
                "Planner produced invalid semantic EDIT steps: " + "; ".join(hard_errors)
            )

        validation_flags = _validate_plan_files(plan, repo_path)
        triad_flags = validate_execution_plan(plan, repo_path, report)
        if triad_flags:
            validation_flags.extend(triad_flags)
        validation_flags.extend(soft_flags)

        if validation_flags:
            plan.flags = list(dict.fromkeys(list(plan.flags) + validation_flags))

        if previous_attempts:
            for att in previous_attempts[-3:]:
                note = f"Avoid: {(att.get('error_summary') or '')[:100]}"
                if note not in plan.self_healing_notes:
                    plan.self_healing_notes.append(note)

        _log.info(
            "complete",
            ticket_id=plan.ticket_id,
            steps=len(plan.execution_steps),
            files=len(plan.files_to_change),
            planner_snapshot_head=plan.planner_snapshot_head,
        )
        return plan
    finally:
        reset_tracing_agent(tracing_token)


def _latest_gate_feedback(state: AgentState, gate_type: str) -> str | None:
    """Latest Gate Feedback for this module."""
    gates = state.get("human_gates") or []
    for gate in reversed(gates):
        if gate.get("gate_type") == gate_type and gate.get("status") == "REVISE":
            feedback = gate.get("feedback")
            if feedback:
                return feedback
    return None


@traceable(name="Planner Agent — TODO Generation")
def planner_node(state: AgentState) -> AgentState:
    """Planner Node for this module."""
    now = datetime.now(timezone.utc).isoformat()
    research_raw = state.get("research_output")
    if not research_raw:
        return {
            **state,
            "current_phase": "PLANNING",
            "exit_message": "Planner input missing: research_output",
            "updated_at": now,
        }

    try:
        report = ResearchReport.model_validate(research_raw)
    except ValidationError as e:
        return {
            **state,
            "current_phase": "PLANNING",
            "exit_message": f"Planner input validation failed: {e}",
            "updated_at": now,
        }

    repo_path = state.get("repo_path") or "."
    repo_profile = state.get("repo_profile") or {}
    gate_feedback = _latest_gate_feedback(state, "PLANNING")
    if gate_feedback:
        _log.info("rerun", gate="PLANNING", feedback_preview=gate_feedback[:200])
    try:
        plan = run_planner(
            report,
            repo_path,
            routing_route=state.get("routing_route") or "FULL",
            previous_attempts=state.get("previous_attempts"),
            gate_feedback=gate_feedback,
            ticket=state.get("jira_ticket"),
            git_target_branch=state.get("git_target_branch"),
            repo_type=repo_profile.get("repo_type"),
        )
        plan_output = _plan_to_output(plan)
    except Exception as e:
        return {
            **state,
            "current_phase": "PLANNING",
            "exit_message": f"Planner failed: {e}",
            "updated_at": now,
        }

    return {
        **state,
        "plan_output": plan_output,
        "current_phase": "PLANNING",
        "prompt_version": "planner-v2.0-semantic",
        "updated_at": now,
    }


def plan_hash(plan: PlanOutput) -> str:
    """Plan Hash for this module."""
    return hashlib.sha256(json.dumps(plan, sort_keys=True, default=str).encode()).hexdigest()[:16]
