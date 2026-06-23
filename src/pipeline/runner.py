"""End-to-end pipeline — Research → Planner → Executor with transition logging."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Literal

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
from src.core.schemas import ExecutionPlan, ExecutionStep, ResearchReport, RoutingDecision
from src.core.state import JiraTicket, ResearchOutput
from src.core.token_usage import start_pipeline_tracking, stop_pipeline_tracking
from src.tools.base import emit_event

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


def build_mock_security_plan(ticket_id: str, repo_path: str) -> ExecutionPlan:
    """Deterministic ExecutionPlan for mock-security demo — full grounded patches."""
    demo_rel = str(MOCK_SECURITY_TARGET)
    source_path = Path(repo_path) / demo_rel
    content = source_path.read_text(encoding="utf-8")
    match = re.search(r"(def search_users[\s\S]*?)(?=\n\ndef |\Z)", content)
    old_string = match.group(1).rstrip() if match else content
    new_string = (
        'def search_users(query: str) -> list[dict[str, Any]]:\n'
        '    """Search users — safe parameterized SQL pattern for demo."""\n'
        '    sql = "SELECT * FROM users WHERE name LIKE ?"\n'
        '    params = (f"%{query}%",)\n'
        '    return [{"sql": sql, "params": params, "query": query}]'
    )
    return ExecutionPlan(
        ticket_id=ticket_id,
        root_hypothesis="Fix SQL injection by parameterizing UserSearchController search query",
        files_to_change=[demo_rel],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where=demo_rel,
                what="Read demo target before edit",
                why="Ground old_string from disk",
                validate="File read succeeds",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                where=demo_rel,
                what="Replace f-string SQL with parameterized query",
                why="Remove SQL injection vulnerability",
                validate='grep for LIKE ?',
                old_string=old_string,
                new_string=new_string,
                intent_marker="LIKE ?",
                change="Parameterize SQL and bind query via params tuple",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                where=demo_rel,
                what="Confirm parameterized query present",
                why="Ensure fix applied",
                validate='grep for LIKE ?; grep for params =',
                intent_marker="LIKE ?",
            ),
        ],
        write_tests=False,
        create_files=False,
        risk_level="high",
        flags=["mock_security_deterministic_plan"],
    )

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
    token_usage: dict[str, Any] | None = None


PipelineProgressCallback = Callable[[str, dict[str, Any]], None]


def _emit_progress(event: str, payload: dict[str, Any], on_progress: PipelineProgressCallback | None) -> None:
    emit_event(f"pipeline_{event}", payload)
    if on_progress:
        on_progress(event, payload)


def _log_node_start(node: str, on_progress: PipelineProgressCallback | None = None) -> None:
    _log.info("node_start", node=node)
    _emit_progress("node_start", {"node": node}, on_progress)


def _log_handoff(
    source: str,
    target: str,
    payload_shape: str,
    on_progress: PipelineProgressCallback | None = None,
) -> None:
    _log.info("handoff", source=source, target=target, payload_shape=payload_shape)
    _emit_progress(
        "handoff",
        {"source": source, "target": target, "payload_shape": payload_shape},
        on_progress,
    )


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
    on_progress: PipelineProgressCallback | None = None,
    llm_research: BaseChatModel | None = None,
    llm_planner: BaseChatModel | None = None,
    llm_orchestrator: BaseChatModel | None = None,
) -> PipelineResult:
    """Run Research → Planner → Executor; stop on first stage failure."""
    configure_logging()
    repo_path = str(repo_path)
    tracker = start_pipeline_tracking()
    result: PipelineResult | None = None
    try:
        result = _run_pipeline_body(
            ticket_id,
            repo_path,
            mock_security=mock_security,
            user_prompt=user_prompt,
            dry_run=dry_run,
            with_gates=with_gates,
            on_progress=on_progress,
            llm_research=llm_research,
            llm_planner=llm_planner,
            llm_orchestrator=llm_orchestrator,
        )
        return result
    finally:
        active = stop_pipeline_tracking() or tracker
        snapshot = active.to_dict()
        active.log_summary()
        if result is not None:
            result.token_usage = snapshot
            if on_progress and snapshot.get("total_tokens", 0) > 0:
                on_progress("token_usage", snapshot)


def _run_pipeline_body(
    ticket_id: str,
    repo_path: str,
    *,
    mock_security: bool = False,
    user_prompt: str = "",
    dry_run: bool = False,
    with_gates: bool = False,
    on_progress: PipelineProgressCallback | None = None,
    llm_research: BaseChatModel | None = None,
    llm_planner: BaseChatModel | None = None,
    llm_orchestrator: BaseChatModel | None = None,
) -> PipelineResult:

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
    _emit_progress(
        "routing",
        {
            "route": decision.routing_route,
            "risk_level": decision.risk_level,
            "reason": decision.reasoning[:200],
        },
        on_progress,
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
        _log_node_start("research", on_progress)
        _log_handoff("research", "planner", "ResearchOutput", on_progress)
        _log_node_start("planner", on_progress)
        _log_handoff("planner", "executor", "ExecutionPlan", on_progress)
        _log_node_start("executor", on_progress)
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
        _log_node_start("research", on_progress)
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
            if mock_security and not research_output.get("affected_files"):
                demo_path = str(MOCK_SECURITY_TARGET)
                research_output["affected_files"] = [
                    {
                        "path": demo_path,
                        "reason": "Mock security demo target (injected — research omitted path)",
                        "evidence_strength": "strong",
                    }
                ]
        except Exception as e:
            _log_stage_failed("research", str(e))
            raise PipelineError("research", str(e)) from e
        _log_handoff("research", "planner", "ResearchOutput", on_progress)

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
        _log_handoff("research", "planner", "ResearchOutput", on_progress)

    _log_node_start("planner", on_progress)
    try:
        report_model = ResearchReport.model_validate(dict(research_output))
        if mock_security:
            plan = build_mock_security_plan(ticket_id, repo_path)
            _orch_log.info("mock_security_deterministic_plan", steps=len(plan.execution_steps))
        else:
            plan = run_planner(
                report_model,
                repo_path,
                routing_route=decision.routing_route,
                llm=llm_planner,
            )
    except Exception as e:
        _log_stage_failed("planner", str(e))
        raise PipelineError("planner", str(e)) from e
    _log_handoff("planner", "executor", "ExecutionPlan", on_progress)

    _log_node_start("executor", on_progress)
    try:
        execution = run_executor(plan, repo_path)
    except Exception as e:
        _log_stage_failed("executor", str(e))
        raise PipelineError("executor", str(e)) from e

    _orch_log.info("pipeline_complete", ticket_id=ticket_id, steps=execution.steps_completed)
    _emit_progress(
        "complete",
        {
            "ticket_id": ticket_id,
            "steps": execution.steps_completed,
            "changed_files": execution.changed_files,
        },
        on_progress,
    )
    return PipelineResult(
        success=True,
        routing_route=decision.routing_route,
        ticket_id=ticket_id,
        repo_path=repo_path,
        research_output=research_output,
        plan=plan,
        execution=execution,
    )
