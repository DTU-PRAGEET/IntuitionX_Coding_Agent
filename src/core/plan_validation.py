"""Execution plan validation — triad ordering, path checks, and instruction EDIT integrity."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from src.core.schemas import ExecutionPlan, ExecutionStep, ResearchReport


def is_instruction_edit(step: ExecutionStep | dict[str, Any]) -> bool:
    """True when step uses an instruction-based EDIT (not legacy patches)."""
    if isinstance(step, ExecutionStep):
        return bool((step.instruction or "").strip())
    return bool((step.get("instruction") or "").strip())


def sync_files_to_change_from_edit_steps(plan: ExecutionPlan) -> ExecutionPlan:
    """Union EDIT-step file paths into files_to_change (planner LLM often omits some)."""
    edit_files = [
        step.file
        for step in plan.execution_steps
        if step.action == "EDIT" and step.file
    ]
    if not edit_files:
        return plan
    merged = list(dict.fromkeys([*plan.files_to_change, *edit_files]))
    if merged != plan.files_to_change:
        plan.files_to_change = merged
        plan.flags = list(
            dict.fromkeys([*plan.flags, "files_to_change_synced_from_edit_steps"])
        )
    return plan


def validate_plan_triad(plan: ExecutionPlan) -> list[str]:
    """Validate plan triad."""
    errors: list[str] = []
    steps = plan.execution_steps
    for i, step in enumerate(steps):
        if step.action != "EDIT":
            continue
        path = step.file
        if i == 0 or steps[i - 1].action != "READ" or steps[i - 1].file != path:
            errors.append(f"step {step.step_id}: EDIT requires preceding READ on {path}")
        if i + 1 >= len(steps) or steps[i + 1].action != "VERIFY" or steps[i + 1].file != path:
            errors.append(f"step {step.step_id}: EDIT requires following VERIFY on {path}")
        if not step.intent_marker:
            errors.append(f"step {step.step_id}: EDIT requires intent_marker")
    return errors


def validate_plan_paths(
    plan: ExecutionPlan,
    research: ResearchReport | None,
    repo_path: str,
) -> list[str]:
    """Validate plan paths."""
    errors: list[str] = []
    repo = Path(repo_path)
    research_paths: set[str] = set()
    if research is not None:
        research_paths = {f.path for f in research.affected_files}

    for step in plan.execution_steps:
        p = step.file
        if research_paths and p not in research_paths and p not in plan.files_to_change:
            errors.append(f"step {step.step_id}: path {p!r} not in research affected_files")
        if not (repo / p).exists() and not plan.create_files and step.action != "RUN_TESTS":
            errors.append(f"step {step.step_id}: path {p!r} not found in repo")
    return errors


def validate_instruction_edits(
    plan: ExecutionPlan,
    repo_path: str,
) -> tuple[list[str], list[str]]:
    """Return (hard_errors, soft_flags) for instruction-based EDIT steps — no symbol resolution."""
    hard: list[str] = []
    soft: list[str] = []
    root = Path(repo_path)

    for step in plan.execution_steps:
        if step.action != "EDIT":
            continue
        sid = step.step_id
        path = step.file

        if not (step.instruction or "").strip():
            hard.append(f"step {sid}: EDIT missing instruction")
        if not (step.intent_marker or "").strip():
            hard.append(f"step {sid}: EDIT missing intent_marker")
        if not step.target_type:
            hard.append(f"step {sid}: EDIT missing target_type")
        elif step.target_type != "file" and not (step.target_name or "").strip():
            hard.append(f"step {sid}: EDIT missing target_name for {step.target_type}")
        if step.old_string or step.new_string:
            hard.append(f"step {sid}: legacy patch fields deprecated; use instruction")

        full = root / path
        if not full.is_file() and not plan.create_files:
            hard.append(f"step {sid}: file {path!r} not found")

    return hard, soft


def validate_execution_plan(
    plan: ExecutionPlan,
    repo_path: str,
    research: ResearchReport | None = None,
) -> list[str]:
    """Validate execution plan."""
    return validate_plan_triad(plan) + validate_plan_paths(plan, research, repo_path)


# --- M1.3 commented out: validate_execution_plan_strict (see docs/cleanup-audit.md) ---
# def validate_execution_plan_strict(
#     plan: ExecutionPlan,
#     repo_path: str,
#     research: ResearchReport | None = None,
# ) -> list[str]:
#     """All validation errors including hard instruction EDIT failures."""
#     triad_and_paths = validate_execution_plan(plan, repo_path, research)
#     hard, _soft = validate_instruction_edits(plan, repo_path)
#     return triad_and_paths + hard
# --- end M1.3: validate_execution_plan_strict ---
