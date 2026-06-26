"""Tests for milestone 1.3 — Cursor research/plan sub-agents."""

from __future__ import annotations

from pathlib import Path

from src.agents.orchestrator.tools.jira import mock_security_jira_ticket
from src.agents.planner.sub_agent import CursorPlanSubAgent
from src.agents.research.research_agent import ResearchAgent
from src.agents.research.sub_agent import CursorResearchSubAgent
from src.core.agent_context import AgentContext
from src.agents.executor.sub_agent import CursorImplementSubAgent
from src.core.orchestrator_loop import PipelineOrchestrator, StubImplementAgent, run_m13_chain, run_m14_chain
from src.core.schemas import FixPlan
from src.integrations.cursor.gateway import DryRunCursorGateway, get_cursor_gateway
from src.integrations.cursor.plan_runner import run_cursor_plan
from src.integrations.cursor.research_runner import mock_research_report, report_to_research_output


def test_mock_research_report_returns_structured_output():
    ticket = mock_security_jira_ticket("SEC-R1")
    report = mock_research_report(ticket)
    assert report.ticket_id == "SEC-R1"
    assert report.affected_files
    assert report.hypotheses


def test_mock_cursor_plan_produces_fix_plan():
    ticket = mock_security_jira_ticket("SEC-P1")
    report = mock_research_report(ticket)
    research = report_to_research_output(report)
    plan = run_cursor_plan(research, str(Path(".").resolve()), dry_run=True)
    assert isinstance(plan, FixPlan)
    assert plan.targets
    assert plan.files_to_change
    assert plan.primary_fix_type == "parameterize_sql"


def test_research_sub_agent_attaches_research_output():
    ticket = mock_security_jira_ticket("SEC-R2")
    ctx = AgentContext.from_ticket(ticket, repo_path=str(Path(".").resolve()))
    agent = CursorResearchSubAgent(dry_run=True)
    out = agent.run(ctx)
    ro = out.metadata["research_output"]
    assert ro["ticket_id"] == "SEC-R2"
    assert ro["affected_files"]


def test_plan_sub_agent_attaches_fix_plan():
    ticket = mock_security_jira_ticket("SEC-P2")
    ctx = AgentContext.from_ticket(ticket, repo_path=str(Path(".").resolve()))
    ctx = CursorResearchSubAgent(dry_run=True).run(ctx)
    ctx = CursorPlanSubAgent(dry_run=True).run(ctx)
    assert ctx.metadata["fix_plan"]["targets"]
    assert ctx.metadata["plan_output"]["execution_steps"]


def test_m13_orchestrator_e2e_typed_chain():
    result = run_m13_chain("SEC-E2E", dry_run=True)
    assert result.success is True
    assert result.routing_route == "FULL"
    assert result.research.get("affected_files")
    assert result.fix_plan.get("primary_fix_type")
    assert result.fix_plan.get("targets")
    assert result.plan.get("files_to_change")
    assert result.patch.get("status") == "success"
    typed = result.typed_fix_plan()
    assert typed.ticket_id == "SEC-E2E"


def test_m13_pipeline_wires_three_agents_in_sequence():
    repo = str(Path(".").resolve())
    gateway = get_cursor_gateway(repo, dry_run=True)
    orch = PipelineOrchestrator(
        research=CursorResearchSubAgent(repo_path=repo, dry_run=True, gateway=gateway),
        planner=CursorPlanSubAgent(repo_path=repo, dry_run=True, gateway=gateway),
        implement=StubImplementAgent(),
        cursor_gateway=gateway,
    )
    ticket = mock_security_jira_ticket("SEC-SEQ")
    result = orch.run(ticket, repo_path=repo)
    assert result.success
    assert result.typed_fix_plan().ticket_id == "SEC-SEQ"
    assert result.typed_research()["ticket_id"] == "SEC-SEQ"


def test_research_agent_accepts_explicit_gateway():
    ticket = mock_security_jira_ticket("SEC-GW")
    repo = str(Path(".").resolve())
    gw = DryRunCursorGateway(repo)
    report = ResearchAgent(repo, gateway=gw, dry_run=True).run(ticket)
    assert report.ticket_id == "SEC-GW"
    research = report_to_research_output(report)
    plan = run_cursor_plan(research, repo, dry_run=False, gateway=gw)
    assert isinstance(plan, FixPlan)


def test_m14_orchestrator_e2e_typed_chain():
    result = run_m14_chain("SEC-M14-E2E", dry_run=True)
    assert result.success is True
    assert result.research.get("affected_files")
    assert result.fix_plan.get("targets")
    assert result.patch.get("status") in ("dry_run", "success", "partial")
    assert result.patch.get("ticket_id") == "SEC-M14-E2E"


def test_m14_implement_sub_agent_attaches_patch():
    ticket = mock_security_jira_ticket("SEC-IMP")
    repo = str(Path(".").resolve())
    ctx = AgentContext.from_ticket(ticket, repo_path=repo)
    ctx = CursorResearchSubAgent(dry_run=True).run(ctx)
    ctx = CursorPlanSubAgent(dry_run=True).run(ctx)
    ctx = CursorImplementSubAgent(repo_path=repo, dry_run=True).run(ctx)
    patch = ctx.metadata["patch_output"]
    assert patch["ticket_id"] == "SEC-IMP"
    assert patch["status"] == "dry_run"


def test_agent_context_accessors():
    ticket = mock_security_jira_ticket("SEC-ACC")
    ctx = AgentContext.from_ticket(ticket, repo_path=str(Path(".").resolve()))
    ctx = CursorResearchSubAgent(dry_run=True).run(ctx)
    assert ctx.research_output["ticket_id"] == "SEC-ACC"
    assert ctx.execution_metadata == {}
    ctx.append_cursor_history({"method": "search", "status": "dry_run"})
    assert len(ctx.cursor_history) == 1
