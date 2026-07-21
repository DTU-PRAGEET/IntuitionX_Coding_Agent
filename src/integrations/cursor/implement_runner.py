"""Cursor-backed implement runner — FixPlan → CursorGateway.edit() → PatchResult."""

from __future__ import annotations

from pathlib import Path

from src.core.logging import get_logger
from src.core.schemas import FixPlan, PatchResult
from src.core.skill_manager import LoadedSkill, SkillRegistry
from src.core.state import ResearchOutput
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway

_log = get_logger("cursor_implement")


def build_edit_instruction(
    fix_plan: FixPlan,
    research: ResearchOutput | None = None,
    skills: list[LoadedSkill] | None = None,
) -> str:
    """Compose natural-language edit brief for Cursor."""
    parts: list[str] = [
        f"Ticket: {fix_plan.ticket_id}",
        f"Root cause: {fix_plan.root_cause}",
        f"Primary fix type: {fix_plan.primary_fix_type}",
        "",
        "Targets:",
    ]
    for target in fix_plan.targets:
        parts.append(
            f"- {target.file_path} (lines {target.start_line}-{target.end_line}): "
            f"{target.fix_type} — {target.rationale}"
        )
    if fix_plan.recommendations:
        parts.append("\nRecommendations:")
        for rec in fix_plan.recommendations:
            parts.append(f"- {rec}")
    if fix_plan.evidence:
        parts.append("\nEvidence:")
        for ev in fix_plan.evidence[:5]:
            parts.append(f"- {ev}")
    if research:
        summary = (research.get("research_summary") or "")[:600]
        if summary:
            parts.append(f"\nResearch summary:\n{summary}")
    if skills:
        skill_text = SkillRegistry.format_for_prompt(skills)
        if skill_text:
            parts.append(f"\n{skill_text}")
    parts.append(
        "\nApply the fix to the listed files only. "
        "Do not modify unrelated files. Preserve code style."
    )
    return "\n".join(parts)


def mock_patch_result(
    fix_plan: FixPlan,
    *,
    worktree_path: str = "",
    branch_name: str = "",
) -> PatchResult:
    """Deterministic PatchResult for dry-run / tests."""
    expected = list(fix_plan.files_to_change)
    return PatchResult(
        ticket_id=fix_plan.ticket_id,
        status="dry_run",
        changed_files=expected,
        expected_files=expected,
        git_diff_summary="stub diff — dry run, no files modified",
        worktree_path=worktree_path,
        branch_name=branch_name,
        flags=["cursor_implement_dry_run"],
        cursor_status="dry_run",
    )


def validate_patch(
    fix_plan: FixPlan,
    *,
    cursor_status: str,
    changed_files: list[str],
    git_diff_stat: str,
    dry_run: bool,
    exit_code: int,
    errors: list[str] | None = None,
) -> PatchResult:
    """Agent-owned validation after Cursor completes editing."""
    expected = list(fix_plan.files_to_change)
    err_list = list(errors or [])
    flags: list[str] = []

    if dry_run:
        return mock_patch_result(fix_plan)

    ok_status = cursor_status in ("finished", "completed", "success")
    if not ok_status:
        err_list.append(f"Cursor returned status={cursor_status!r}")
        flags.append("cursor_edit_failed")
    if exit_code != 0:
        err_list.append(f"Cursor exit_code={exit_code}")
        flags.append("cursor_nonzero_exit")

    missing = [f for f in expected if f not in changed_files]
    if expected and missing and changed_files:
        flags.append("unexpected_file_set")
    if expected and not changed_files and "(no diff)" in git_diff_stat:
        err_list.append("No files changed after edit")
        flags.append("no_diff")

    if err_list and changed_files:
        status = "partial"
    elif err_list:
        status = "failed"
    else:
        status = "success"

    return PatchResult(
        ticket_id=fix_plan.ticket_id,
        status=status,  # type: ignore[arg-type]
        changed_files=changed_files,
        expected_files=expected,
        git_diff_summary=git_diff_stat,
        flags=flags,
        cursor_status=cursor_status,
        errors=err_list,
    )
