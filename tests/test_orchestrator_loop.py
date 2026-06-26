"""Tests for milestone 1.2 PipelineOrchestrator dummy loop."""

from __future__ import annotations

from src.agents.orchestrator.tools.jira import mock_security_jira_ticket
from src.core.agent_context import AgentContext
from src.core.orchestrator_loop import (
    PipelineOrchestrator,
    StubImplementAgent,
    StubPlannerAgent,
    StubResearchAgent,
    run_dummy_loop,
)
from src.core.sub_agent import SubAgent


def test_agent_context_from_ticket():
    ticket = mock_security_jira_ticket("STUB-1")
    ctx = AgentContext.from_ticket(ticket, user_prompt="fix sqli")
    assert ctx.metadata["ticket_id"] == "STUB-1"
    assert ctx.bug_description
    assert isinstance(ctx.file_paths, list)


def test_stub_research_attaches_output():
    ticket = mock_security_jira_ticket("STUB-2")
    ctx = AgentContext.from_ticket(ticket)
    out = StubResearchAgent().run(ctx)
    assert "research_output" in out.metadata
    assert out.metadata["research_output"]["affected_files"]


def test_pipeline_orchestrator_dummy_loop():
    result = run_dummy_loop("STUB-003")
    assert result.success is True
    assert result.routing_route == "FULL"
    assert result.research.get("affected_files")
    assert result.fix_plan.get("targets") or result.plan.get("execution_steps")
    assert result.patch.get("status") == "success"


def test_sub_agent_abc():
    orch = PipelineOrchestrator(
        research=StubResearchAgent(),
        planner=StubPlannerAgent(),
        implement=StubImplementAgent(),
    )
    ticket = mock_security_jira_ticket("STUB-004")
    result = orch.run(ticket)
    assert isinstance(result.success, bool)
    assert SubAgent in StubResearchAgent.__mro__
