"""Plan SubAgent — ResearchOutput → FixPlan via CursorGateway (milestone 1.3)."""

from __future__ import annotations

from pathlib import Path

from src.core.agent_context import AgentContext
from src.core.logging import get_logger
from src.core.state import ResearchOutput
from src.core.sub_agent import SubAgent
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway
from src.integrations.cursor.plan_runner import fix_plan_to_plan_output, run_cursor_plan

_log = get_logger("planner_sub_agent")


class CursorPlanSubAgent(SubAgent):
    """Takes ResearchOutput from context; produces FixPlan with paths, lines, fix type."""

    name = "planner"

    def __init__(
        self,
        *,
        repo_path: str | None = None,
        dry_run: bool = False,
        gateway: CursorGateway | None = None,
    ):
        self._repo_path = repo_path
        self._dry_run = dry_run
        self._gateway = gateway

    def run(self, context: AgentContext) -> AgentContext:
        research: ResearchOutput | None = context.metadata.get("research_output")
        repo_path = self._repo_path or context.metadata.get("repo_path")

        if not research:
            empty = {
                "ticket_id": context.metadata.get("ticket_id", "UNKNOWN"),
                "root_cause": "Missing research_output",
                "primary_fix_type": "other",
                "targets": [],
                "files_to_change": [],
                "recommendations": [],
                "evidence": [],
                "flags": ["missing_research"],
            }
            context.metadata["fix_plan"] = empty
            context.metadata["plan_output"] = {"execution_steps": [], "flags": ["missing_research"]}
            return context

        if not repo_path:
            fix = run_cursor_plan(research, ".", dry_run=True)
            fix.flags.append("missing_repo_path")
        else:
            gateway = self._gateway
            if gateway is None and not self._dry_run:
                gateway = get_cursor_gateway(Path(repo_path), dry_run=False)
            _log.info(
                "planner_sub_agent_start",
                ticket_id=research.get("ticket_id"),
                repo_path=repo_path,
                dry_run=self._dry_run,
            )
            fix = run_cursor_plan(
                research,
                repo_path,
                dry_run=self._dry_run,
                gateway=gateway,
                ticket=context.metadata.get("jira_ticket"),
            )

        plan_out = fix_plan_to_plan_output(fix)
        ctx = context.with_phase_output("plan", plan_out)
        ctx.metadata["fix_plan"] = fix.model_dump()
        return ctx
