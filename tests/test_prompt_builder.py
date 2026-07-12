"""Tests for PromptBuilder progressive disclosure."""

from __future__ import annotations

from pathlib import Path

from src.core.skill_manager import PromptBuilder, module_skills_dir
from tests.helpers import make_security_ticket

ORCH_SKILLS = module_skills_dir("orchestrator")
RESEARCH_SKILLS = module_skills_dir("research")


def test_build_orchestrator_prompt_once():
    """Test Build Orchestrator Prompt Once for this module."""
    prompt, manifest = PromptBuilder.build(
        "orchestrator",
        ORCH_SKILLS,
        {"jira_ticket_id": "PLAT-1"},
        include_references=False,
    )
    assert manifest.name == "orchestrator"
    assert "decision flow" in prompt.lower() or "routing" in prompt.lower()
    assert prompt.count("## CONTEXT") == 1


def test_build_system_only_no_context():
    """Test Build System Only No Context for this module."""
    body, manifest = PromptBuilder.build_system_only(RESEARCH_SKILLS)
    assert manifest.tool_budget == 13
    assert "## CONTEXT" not in body


def test_research_includes_security_reference():
    """Test Research Includes Security Reference for this module."""
    ticket = make_security_ticket("SEC-1")
    prompt, _ = PromptBuilder.build(
        "research",
        RESEARCH_SKILLS,
        {"ticket_id": "SEC-1"},
        ticket=ticket,
        include_references=True,
    )
    assert "REFERENCE: security_patterns" in prompt
    assert prompt.count("## CONTEXT") == 1
