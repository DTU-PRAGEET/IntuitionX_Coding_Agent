"""Tests for research deep_runner deepagents skills integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.agents.orchestrator.tools.jira import mock_security_jira_ticket
from src.agents.research.deep_runner import (
    DEEPAGENTS_SKILL_SOURCE,
    SKILL_DIR,
    SKILLS_ROOT,
    build_research_user_message,
    load_skill_manifest_safe,
)
from src.agents.research.memory import InMemoryResearchMemory

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_skill_directory_layout_for_deepagents():
    assert (SKILL_DIR / "SKILL.md").is_file()
    assert (SKILL_DIR / "references" / "security_patterns.md").is_file()
    manifest = load_skill_manifest_safe()
    assert manifest.name == "research"
    assert manifest.tool_budget == 12


def test_build_research_user_message_excludes_skill_body(tmp_path):
    ticket = mock_security_jira_ticket("SEC-4421")
    memory = InMemoryResearchMemory()
    message = build_research_user_message(
        ticket,
        str(tmp_path),
        None,
        ["search"],
        "",
        memory,
    )
    assert "## CONTEXT" in message
    assert "Research Agent" not in message  # SKILL.md body title
    assert "Workflow (Follow In Order)" not in message


@patch("deepagents.create_deep_agent")
def test_build_deep_agent_uses_skills_and_backend(mock_create):
    from src.agents.research.deep_runner import _build_deep_agent
    from src.core.schemas import ResearchReport

    mock_create.return_value = MagicMock()
    llm = MagicMock()
    _build_deep_agent(llm, [], tool_budget=12)

    mock_create.assert_called_once()
    kwargs = mock_create.call_args.kwargs
    assert kwargs["skills"] == [DEEPAGENTS_SKILL_SOURCE]
    assert kwargs["backend"] is not None
    assert kwargs["response_format"] is ResearchReport
    assert "research skill" in kwargs["system_prompt"].lower()
