"""End-to-end pipeline — Research → Planner → Executor with transition logging."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from pathlib import Path

from langchain_core.language_models import BaseChatModel

from src.agents.executor.agent import ExecutionResult, run_executor
from src.agents.orchestrator.tools.jira import (
    fetch_jira_ticket,
    is_ticket_done,
    mock_security_jira_ticket,
)
from src.agents.planner.agent import run_planner
from src.agents.research.agent import _report_to_output
from src.agents.research.deep_runner import run_research
from src.agents.research.memory import InMemoryResearchMemory
from src.agents.research.tools.file_discovery import extract_anchors
from src.agents.research.tools.repo_profile import profile_repository
from src.core.logging import configure_logging, get_logger
from src.core.schemas import ExecutionPlan, ResearchReport, RoutingDecision
# from src.core.debug_log import agent_debug
from src.core.state import JiraTicket, ResearchOutput

_log = get_logger("graph")
_orch_log = get_logger("orchestrator")

MOCK_SECURITY_FIXTURE = (
    Path(__file__).resolve().parent / "fixtures/user_search_controller_vulnerable.py"
)
MOCK_SECURITY_TARGET = Path("src/agents/planner/fixtures/demo/UserSearchController.py")


def _reset_mock_security_fixture(repo_path: str) -> None:
    """Restore demo SQLi fixture so --mock-security runs are repeatable."""
    root = Path(repo_path)
    source = MOCK_SECURITY_FIXTURE
    target = root / MOCK_SECURITY_TARGET
    if not source.is_file():
        _orch_log.warning("mock_fixture_missing", path=str(source))
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    _orch_log.info("mock_fixture_reset", path=str(target))

StageName = Literal["research", "planner", "executor", "orchestrator"]


class PipelineError(Exception):
    """Pipeline stopped at a stage with localized context."""

    def __init__(self, stage: StageName, message: str) -> None:
        self.stage = stage
        self.message = message
        super().__init__(f"[orchestrator] stage_failed={stage} error={message}")


@dataclass
class PipelineResult:
    success: bool
    routing_route: str
    ticket_id: str
    repo_path: str
    research_output: ResearchOutput | None = None
    plan: ExecutionPlan | None = None
    execution: ExecutionResult | None = None
    stage_failed: StageName | None = None
    error: str | None = None
    dry_run: bool = False


def _log_node_start(node: str) -> None:
    _log.info("node_start", node=node)


def _log_handoff(source: str, target: str, payload_shape: str) -> None:
    _log.info("handoff", source=source, target=target, payload_shape=payload_shape)


def _log_stage_failed(stage: StageName, error: str) -> None:
    _orch_log.error("stage_failed", stage=stage, error=error)


def resolve_routing(
    ticket: JiraTicket,
    *,
    mock_security: bool = False,
    llm: BaseChatModel | None = None,
) -> RoutingDecision:
    """Deterministic or LLM routing — mock security tickets always use FULL."""
    if mock_security:
        return RoutingDecision(
            routing_route="FULL",
            risk_level="HIGH",
            reasoning="Mock security ticket — forced FULL pipeline for demo",
        )
    if is_ticket_done(ticket):
        return RoutingDecision(
            routing_route="EXIT",
            risk_level="LOW",
            reasoning=f"Ticket {ticket['issue_id']} is already {ticket['status']}",
        )

    from src.agents.orchestrator.agent import run_orchestrator_routing

    return run_orchestrator_routing(ticket, llm=llm)


def run_pipeline(
    ticket_id: str,
    repo_path: str,
    *,
    mock_security: bool = False,
    user_prompt: str = "",
    dry_run: bool = False,
    with_gates: bool = False,
    llm_research: BaseChatModel | None = None,
    llm_planner: BaseChatModel | None = None,
    llm_orchestrator: BaseChatModel | None = None,
) -> PipelineResult:
    """Run Research → Planner → Executor; stop on first stage failure."""
    configure_logging()
    repo_path = str(repo_path)

    if mock_security and not dry_run:
        _reset_mock_security_fixture(repo_path)

    try:
        if mock_security:
            ticket = mock_security_jira_ticket(ticket_id)
        else:
            ticket = fetch_jira_ticket(ticket_id)
    except Exception as e:
        _log_stage_failed("orchestrator", str(e))
        raise PipelineError("orchestrator", f"Failed to load ticket {ticket_id!r}: {e}") from e

    profile = profile_repository(repo_path)
    decision = resolve_routing(ticket, mock_security=mock_security, llm=llm_orchestrator)
    _orch_log.info(
        "routing",
        route=decision.routing_route,
        risk_level=decision.risk_level,
        reason=decision.reasoning[:200],
    )

    if decision.routing_route == "EXIT":
        return PipelineResult(
            success=False,
            routing_route="EXIT",
            ticket_id=ticket_id,
            repo_path=repo_path,
            error=decision.reasoning,
            stage_failed="orchestrator",
        )

    if with_gates:
        _log.info("gates_enabled", note="HITL gates auto-approved in CLI demo mode")

    if dry_run:
        _log_node_start("research")
        _log_handoff("research", "planner", "ResearchOutput")
        _log_node_start("planner")
        _log_handoff("planner", "executor", "ExecutionPlan")
        _log_node_start("executor")
        return PipelineResult(
            success=True,
            routing_route=decision.routing_route,
            ticket_id=ticket_id,
            repo_path=repo_path,
            dry_run=True,
        )

    research_output: ResearchOutput | None = None
    plan: ExecutionPlan | None = None
    execution: ExecutionResult | None = None

    if decision.routing_route in ("FULL",):
        _log_node_start("research")
        try:
            memory = InMemoryResearchMemory()
            anchors = extract_anchors(ticket, user_prompt)
            report = run_research(
                ticket=ticket,
                repo_path=repo_path,
                user_prompt=user_prompt,
                repo_profile=profile,
                anchors=anchors,
                memory=memory,
                llm=llm_research,
            )
            research_output = _report_to_output(report)
            # agent_debug(
            #     "A",
            #     "pipeline/runner.py:run_pipeline",
            #     "research_complete",
            #     {
            #         "affected_files": [f.path for f in report.affected_files],
            #         "hypotheses_count": len(report.hypotheses),
            #     },
            # )
            if mock_security and not research_output.get("affected_files"):
                demo_path = str(MOCK_SECURITY_TARGET)
                research_output["affected_files"] = [
                    {
                        "path": demo_path,
                        "reason": "Mock security demo target (injected — research omitted path)",
                        "evidence_strength": "strong",
                    }
                ]
                # agent_debug(
                #     "A",
                #     "pipeline/runner.py:run_pipeline",
                #     "injected_mock_affected_file",
                #     {"path": demo_path},
                # )
        except Exception as e:
            _log_stage_failed("research", str(e))
            raise PipelineError("research", str(e)) from e
        _log_handoff("research", "planner", "ResearchOutput")

    elif decision.routing_route in ("SKIP_RESEARCH", "MICRO"):
        research_output = ResearchOutput(
            ticket_id=ticket_id,
            research_summary=ticket.get("problem_statement", "")[:500],
            hypotheses=[
                {
                    "rank": 1,
                    "hypothesis": ticket.get("title", "Scoped ticket"),
                    "evidence_strength": "moderate",
                    "evidence": ["SKIP_RESEARCH route — no deep research run"],
                }
            ],
            affected_files=[],
            affected_modules=[ticket.get("module", "unknown")],
            module_blast_radius=[],
            architectural_risk="low",
            issue_type_assessment="unknown",
            flags=["Research skipped per routing"],
        )
        _log.info("research_skipped", route=decision.routing_route)
        _log_handoff("research", "planner", "ResearchOutput")

    _log_node_start("planner")
    try:
        report_model = ResearchReport.model_validate(dict(research_output))
        plan = run_planner(
            report_model,
            repo_path,
            routing_route=decision.routing_route,
            llm=llm_planner,
        )
    except Exception as e:
        _log_stage_failed("planner", str(e))
        raise PipelineError("planner", str(e)) from e
    _log_handoff("planner", "executor", "ExecutionPlan")

    _log_node_start("executor")
    try:
        execution = run_executor(plan, repo_path)
    except Exception as e:
        _log_stage_failed("executor", str(e))
        raise PipelineError("executor", str(e)) from e

    _orch_log.info("pipeline_complete", ticket_id=ticket_id, steps=execution.steps_completed)
    return PipelineResult(
        success=True,
        routing_route=decision.routing_route,
        ticket_id=ticket_id,
        repo_path=repo_path,
        research_output=research_output,
        plan=plan,
        execution=execution,
    )
