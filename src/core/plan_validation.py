"""Execution plan validation — triad ordering, path checks, and EDIT patch integrity."""

from __future__ import annotations

from pathlib import Path

from src.core.repo_snapshot import old_string_in_file
from src.core.schemas import ExecutionPlan, ResearchReport

# Minimum new_string length when old_string is longer — detects LLM truncation.
_TRUNCATION_RATIO = 0.6
_TRUNCATION_MIN_NEW_LEN = 80


def align_old_string_to_disk(old_string: str, file_content: str) -> str:
    """Return exact on-disk text when old_string differs only by trailing newlines."""
    if not old_string or not file_content:
        return old_string
    if old_string in file_content:
        return old_string
    if old_string.rstrip("\n") == file_content.rstrip("\n"):
        return file_content
    return old_string


def old_string_occurrence_count(file_content: str, old_string: str) -> int:
    """Count exact matches, then try disk-aligned old_string."""
    if not old_string:
        return 0
    count = file_content.count(old_string)
    if count:
        return count
    aligned = align_old_string_to_disk(old_string, file_content)
    if aligned != old_string:
        return file_content.count(aligned)
    return 0


def is_truncated_patch(old_string: str, new_string: str) -> bool:
    if not old_string or not new_string:
        return False
    if old_string == new_string:
        return True
    # Short patches are valid — only apply ratio check on substantial blocks.
    if len(old_string) < 80 and len(new_string) < 80:
        return False
    threshold = max(len(old_string) * _TRUNCATION_RATIO, _TRUNCATION_MIN_NEW_LEN)
    if len(new_string) < threshold:
        return True
    # Mid-token truncation: old ends abruptly without newline and is not a file suffix.
    if not old_string.endswith("\n") and len(old_string) > 40:
        tail = old_string[-12:]
        if tail.isalpha() or (len(tail) <= 4 and tail.isalnum()):
            return True
    return False


def validate_plan_triad(plan: ExecutionPlan) -> list[str]:
    errors: list[str] = []
    steps = plan.execution_steps
    for i, step in enumerate(steps):
        if step.action != "EDIT":
            continue
        path = step.where
        if i == 0 or steps[i - 1].action != "READ" or steps[i - 1].where != path:
            errors.append(f"step {step.step_id}: EDIT requires preceding READ on {path}")
        if i + 1 >= len(steps) or steps[i + 1].action != "VERIFY" or steps[i + 1].where != path:
            errors.append(f"step {step.step_id}: EDIT requires following VERIFY on {path}")
        if not step.intent_marker:
            errors.append(f"step {step.step_id}: EDIT requires intent_marker")
    return errors


def validate_plan_paths(
    plan: ExecutionPlan,
    research: ResearchReport | None,
    repo_path: str,
) -> list[str]:
    errors: list[str] = []
    repo = Path(repo_path)
    research_paths: set[str] = set()
    if research is not None:
        research_paths = {f.path for f in research.affected_files}

    for step in plan.execution_steps:
        p = step.where
        if research_paths and p not in research_paths and p not in plan.files_to_change:
            errors.append(f"step {step.step_id}: path {p!r} not in research affected_files")
        if not (repo / p).exists() and not plan.create_files and step.action != "RUN_TESTS":
            errors.append(f"step {step.step_id}: path {p!r} not found in repo")
    return errors


def validate_edit_patches(plan: ExecutionPlan, repo_path: str) -> tuple[list[str], list[str]]:
    """Return (hard_errors, soft_flags) for EDIT steps after normalization."""
    hard: list[str] = []
    soft: list[str] = []
    root = Path(repo_path)

    for step in plan.execution_steps:
        if step.action != "EDIT":
            continue
        path = step.where or step.file
        old_s = step.old_string or ""
        new_s = step.new_string or ""

        if not old_s and not plan.create_files:
            hard.append(f"step {step.step_id}: EDIT has empty old_string on existing file {path}")
        if not new_s:
            hard.append(f"step {step.step_id}: EDIT has empty new_string on {path}")
        if old_s == new_s:
            hard.append(
                f"step {step.step_id}: EDIT old_string identical to new_string on {path}"
            )
            continue

        if is_truncated_patch(old_s, new_s):
            hard.append(
                f"step {step.step_id}: EDIT patch appears truncated on {path} "
                f"(old_len={len(old_s)}, new_len={len(new_s)})"
            )

        full = root / path
        if not full.is_file():
            if not plan.create_files:
                hard.append(f"step {step.step_id}: target file {path!r} not found for EDIT")
            continue

        check = old_string_in_file(repo_path, path, old_s)
        if not old_s:
            continue
        try:
            raw = (full).read_text(encoding="utf-8")
        except OSError:
            raw = ""
        count = old_string_occurrence_count(raw, old_s)
        if count == 0:
            aligned = align_old_string_to_disk(old_s, raw)
            if aligned != old_s and aligned in raw:
                soft.append(
                    f"step {step.step_id}: old_string aligned to disk for {path} "
                    f"(trailing newline mismatch)"
                )
                count = raw.count(aligned)
        if count == 0:
            hard.append(
                f"step {step.step_id}: old_string not found in {path} "
                f"(planner snapshot file_sha256={check['file_sha256'][:12]})"
            )
        elif count != 1:
            hard.append(
                f"step {step.step_id}: old_string must appear exactly once in {path}, "
                f"found {count} occurrences"
            )

    return hard, soft


def validate_execution_plan(
    plan: ExecutionPlan,
    repo_path: str,
    research: ResearchReport | None = None,
) -> list[str]:
    return validate_plan_triad(plan) + validate_plan_paths(plan, research, repo_path)


def validate_execution_plan_strict(
    plan: ExecutionPlan,
    repo_path: str,
    research: ResearchReport | None = None,
) -> list[str]:
    """All validation errors including hard EDIT patch failures."""
    triad_and_paths = validate_execution_plan(plan, repo_path, research)
    hard, _soft = validate_edit_patches(plan, repo_path)
    return triad_and_paths + hard
