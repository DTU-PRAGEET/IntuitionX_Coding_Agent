"""Implement SubAgent — FixPlan → CursorGateway.edit() → PatchResult (milestone 1.4)."""

from __future__ import annotations

from pathlib import Path

from src.agents.executor.tools.git_worktree import create_worktree, show_diff, stage_commit_push
from src.agents.executor.tools.mr_create import create_github_pr
from src.core.agent_context import AgentContext
from src.core.logging import get_logger
from src.core.schemas import FixPlan, PatchResult
from src.core.skill_manager import SkillRegistry, get_skill_registry
from src.core.sub_agent import SubAgent
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway
from src.integrations.cursor.implement_runner import mock_patch_result, run_cursor_implement

_log = get_logger("implement_sub_agent")


class CursorImplementSubAgent(SubAgent):
    """Takes FixPlan from context; edits via CursorGateway; returns PatchResult."""

    name = "implement"

    def __init__(
        self,
        *,
        repo_path: str | None = None,
        dry_run: bool = False,
        gateway: CursorGateway | None = None,
        skill_registry: SkillRegistry | None = None,
        create_pr: bool = False,
    ):
        """Initialize CursorImplementSubAgent with the provided arguments."""
        self._repo_path = repo_path
        self._dry_run = dry_run
        self._gateway = gateway
        self._registry = skill_registry or get_skill_registry()
        self._create_pr = create_pr

    def run(self, context: AgentContext) -> AgentContext:
        """Run for CursorImplementSubAgent."""
        fix_raw = context.metadata.get("fix_plan") or {}
        ticket_id = context.metadata.get("ticket_id", "UNKNOWN")
        repo_path = self._repo_path or context.metadata.get("repo_path") or "."

        if not fix_raw:
            empty = PatchResult(
                ticket_id=ticket_id,
                status="failed",
                errors=["missing fix_plan"],
                flags=["missing_fix_plan"],
            )
            return context.with_phase_output("patch", empty.model_dump())

        fix_plan = FixPlan.model_validate(fix_raw)
        ticket = context.metadata.get("jira_ticket")
        research = context.metadata.get("research_output")
        skills = self._registry.select("implement", ticket=ticket, fix_plan=fix_plan)

        if self._dry_run:
            patch = mock_patch_result(fix_plan)
            patch.flags = list(
                dict.fromkeys(list(patch.flags) + [s.name for s in skills if s.module == "security"])
            )
            return context.with_phase_output("patch", patch.model_dump())

        try:
            worktree_info = create_worktree(
                repo_path,
                fix_plan.ticket_id,
                description=(fix_plan.root_cause or "")[:30],
            )
        except Exception as e:
            _log.error("worktree_failed", error=str(e))
            patch = PatchResult(
                ticket_id=fix_plan.ticket_id,
                status="failed",
                expected_files=list(fix_plan.files_to_change),
                errors=[f"worktree: {e}"],
                flags=["worktree_failed"],
            )
            return context.with_phase_output("patch", patch.model_dump())

        worktree_path = worktree_info["worktree_path"]
        branch_name = worktree_info["branch_name"]

        gateway = self._gateway
        if gateway is None:
            gateway = get_cursor_gateway(Path(worktree_path), dry_run=self._dry_run)

        patch, edit_result = run_cursor_implement(
            fix_plan,
            worktree_path,
            dry_run=self._dry_run,
            gateway=gateway,
            skills=skills,
            research=research,
            metadata={
                "ticket_id": fix_plan.ticket_id,
                "branch_name": branch_name,
                **context.execution_metadata,
            },
        )

        diff_summary = show_diff(worktree_path)
        if diff_summary:
            patch.git_diff_summary = diff_summary[:8000]
        patch.worktree_path = worktree_path
        patch.branch_name = branch_name

        if edit_result is not None:
            context.append_cursor_history(edit_result.to_history_entry())

        if self._create_pr and patch.status == "success" and not self._dry_run:
            commit_msg = f"fix({fix_plan.ticket_id}): {fix_plan.root_cause[:72]}"
            push = stage_commit_push(worktree_path, branch_name, commit_msg)
            if push.get("pushed"):
                try:
                    patch.mr_url = create_github_pr(
                        branch_name,
                        title=f"[{fix_plan.ticket_id}] Security fix",
                        body=fix_plan.root_cause,
                    )
                except Exception as e:
                    patch.flags.append(f"pr_failed: {e}")

        _log.info(
            "implement_sub_agent_complete",
            ticket_id=fix_plan.ticket_id,
            status=patch.status,
            changed=len(patch.changed_files),
        )
        return context.with_phase_output("patch", patch.model_dump())
