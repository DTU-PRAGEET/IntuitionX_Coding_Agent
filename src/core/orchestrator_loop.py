"""PipelineOrchestrator — fan-out loop with SubAgent chain (milestones 1.2–1.3)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.agent_context import AgentContext
from src.core.logging import get_logger
from src.core.schemas import FixPlan, RoutingDecision
from src.core.state import JiraTicket, ResearchOutput
from src.core.sub_agent import SubAgent
from src.integrations.cursor.gateway import CursorGateway, get_cursor_gateway

_log = get_logger("orchestrator_loop")


@dataclass
class OrchestratorResult:
    """Typed result from orchestrator.run(bug_ticket)."""

    routing_route: str
    research: dict[str, Any] = field(default_factory=dict)
    fix_plan: dict[str, Any] = field(default_factory=dict)
    plan: dict[str, Any] = field(default_factory=dict)
    patch: dict[str, Any] = field(default_factory=dict)
    success: bool = True
    error: str | None = None
    phase_failed: str | None = None

    def typed_research(self) -> ResearchOutput:
        return ResearchOutput(**self.research)  # type: ignore[typeddict-item]

    def typed_fix_plan(self) -> FixPlan:
        if not self.fix_plan:
            raise ValueError("fix_plan is empty — cannot validate as FixPlan")
        return FixPlan.model_validate(self.fix_plan)


class StubResearchAgent(SubAgent):
    name = "research"

    def run(self, context: AgentContext) -> AgentContext:
        ticket_id = context.metadata.get("ticket_id", "UNKNOWN")
        output = {
            "ticket_id": ticket_id,
            "research_summary": "Stub research: identified demo target file.",
            "hypotheses": [],
            "affected_files": [
                {
                    "path": "src/demo/example.py",
                    "reason": "Stub affected file for milestone 1.2 dummy loop",
                    "evidence_strength": "moderate",
                }
            ],
            "affected_modules": [],
            "module_blast_radius": [],
            "architectural_risk": "low",
            "issue_type_assessment": "code_bug",
            "flags": ["stub_research"],
        }
        return context.with_phase_output("research", output)


class StubPlannerAgent(SubAgent):
    name = "planner"

    def run(self, context: AgentContext) -> AgentContext:
        ticket_id = context.metadata.get("ticket_id", "UNKNOWN")
        files = context.file_paths or ["src/demo/example.py"]
        target = files[0] if files else "src/demo/example.py"
        fix_plan = {
            "ticket_id": ticket_id,
            "root_cause": "Stub plan for milestone 1.2",
            "primary_fix_type": "other",
            "targets": [
                {
                    "file_path": target,
                    "start_line": 1,
                    "end_line": 50,
                    "fix_type": "other",
                    "rationale": "Stub target",
                }
            ],
            "files_to_change": [target],
            "recommendations": [],
            "evidence": [],
            "flags": ["stub_plan"],
        }
        plan_out = {
            "ticket_id": ticket_id,
            "root_hypothesis": fix_plan["root_cause"],
            "files_to_change": [target],
            "execution_steps": [
                {
                    "step_id": 1,
                    "action": "READ",
                    "file": target,
                    "target": "Read target file",
                    "reason": "Stub step",
                    "validate": "File exists",
                }
            ],
            "risk_level": "low",
            "flags": ["stub_plan"],
        }
        ctx = context.with_phase_output("plan", plan_out)
        ctx.metadata["fix_plan"] = fix_plan
        return ctx


class StubImplementAgent(SubAgent):
    name = "implement"

    def run(self, context: AgentContext) -> AgentContext:
        plan = context.metadata.get("plan_output") or {}
        output = {
            "status": "success",
            "changed_files": plan.get("files_to_change") or [],
            "git_diff_summary": "stub diff — no files modified",
            "mr_url": "",
        }
        return context.with_phase_output("patch", output)


class OrchestratorSubAgent(SubAgent):
    """Stub routing — returns FULL for dummy loop (no LLM)."""

    name = "orchestrator"

    def run(self, context: AgentContext) -> AgentContext:
        decision = RoutingDecision(
            routing_route="FULL",
            risk_level="MEDIUM",
            reasoning="Stub routing for orchestrator loop",
        )
        context.metadata["routing_decision"] = decision.model_dump()
        return context


# Backwards-compatible alias
RoutingSubAgent = OrchestratorSubAgent


class PipelineOrchestrator:
    """Receive bug ticket → fan out to sub-agents → collect typed results."""

    def __init__(
        self,
        orchestrator: SubAgent | None = None,
        research: SubAgent | None = None,
        planner: SubAgent | None = None,
        implement: SubAgent | None = None,
        *,
        cursor_gateway: CursorGateway | None = None,
    ):
        self.orchestrator = orchestrator or OrchestratorSubAgent()
        self._cursor_gateway = cursor_gateway
        self.research = research or StubResearchAgent()
        self.planner = planner or StubPlannerAgent()
        self.implement = implement or StubImplementAgent()

    def run(
        self,
        ticket: JiraTicket,
        *,
        user_prompt: str = "",
        repo_path: str | None = None,
    ) -> OrchestratorResult:
        """Run chain: orchestrator → research → plan → implement."""
        ctx = AgentContext.from_ticket(ticket, user_prompt=user_prompt, repo_path=repo_path)
        _log.info("orchestrator_loop_start", ticket_id=ticket.get("issue_id"))

        gateway = self._cursor_gateway
        if gateway is None and repo_path and not isinstance(self.research, StubResearchAgent):
            gateway = get_cursor_gateway(Path(repo_path), dry_run=False)

        phase_failed: str | None = None
        try:
            ctx = self.orchestrator.run(ctx)
            route = ctx.metadata.get("routing_decision", {}).get("routing_route", "FULL")

            if route == "EXIT":
                return OrchestratorResult(
                    routing_route="EXIT",
                    success=True,
                    error=ctx.metadata.get("routing_decision", {}).get("reasoning"),
                )

            if gateway is not None:
                self._inject_gateway(gateway)

            phase_failed = "research"
            ctx = self.research.run(ctx)
            research_out = ctx.metadata.get("research_output") or {}

            phase_failed = "planner"
            ctx = self.planner.run(ctx)
            fix_plan_out = ctx.metadata.get("fix_plan") or {}
            plan_out = ctx.metadata.get("plan_output") or {}

            phase_failed = "implement"
            ctx = self.implement.run(ctx)
            patch_out = ctx.metadata.get("patch_output") or {}

            _log.info("orchestrator_loop_complete", ticket_id=ticket.get("issue_id"), route=route)
            return OrchestratorResult(
                routing_route=route,
                research=research_out,
                fix_plan=fix_plan_out,
                plan=plan_out,
                patch=patch_out,
                success=True,
            )
        except Exception as e:
            _log.error("orchestrator_loop_failed", error=str(e))
            return OrchestratorResult(
                routing_route="EXIT",
                success=False,
                error=str(e),
                phase_failed=phase_failed,
            )

    def _inject_gateway(self, gateway: CursorGateway) -> None:
        for agent in (self.research, self.planner, self.implement):
            if hasattr(agent, "_gateway") and agent._gateway is None:
                agent._gateway = gateway  # type: ignore[attr-defined]
