"""Tests for PromptBuilder progressive disclosure."""

from __future__ import annotations

from pathlib import Path

from src.agents.orchestrator.tools.jira import mock_security_jira_ticket
from src.core.prompt_builder import PromptBuilder

ORCH_SKILLS = Path(__file__).resolve().parents[1] / "src" / "agents" / "orchestrator" / "skills"
RESEARCH_SKILLS = Path(__file__).resolve().parents[1] / "src" / "agents" / "research" / "skills" / "research"


def test_build_orchestrator_prompt_once():
    prompt, manifest = PromptBuilder.build(
        "orchestrator",
        ORCH_SKILLS,
        {"jira_ticket_id": "PLAT-1"},
        include_references=False,
    )
    assert manifest.name == "orchestrator"
    assert "Routing Routes" in prompt
    assert prompt.count("## CONTEXT") == 1


def test_build_system_only_no_context():
    body, manifest = PromptBuilder.build_system_only(RESEARCH_SKILLS)
    assert manifest.tool_budget == 12
    assert "## CONTEXT" not in body


def test_research_includes_security_reference():
    ticket = mock_security_jira_ticket("SEC-1")
    prompt, _ = PromptBuilder.build(
        "research",
        RESEARCH_SKILLS,
        {"ticket_id": "SEC-1"},
        ticket=ticket,
        include_references=True,
    )
    assert "REFERENCE: security_patterns" in prompt
    assert prompt.count("## CONTEXT") == 1
