"""Tests for research deep_runner deepagents skills integration."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from src.agents.research.deep_runner import (
    SKILL_DIR,
    build_research_user_message,
    load_skill_manifest_safe,
)
from src.agents.research.memory import InMemoryResearchMemory
from tests.helpers import make_security_ticket

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_skill_directory_layout_for_deepagents():
    """Test Skill Directory Layout For Deepagents for this module."""
    assert (SKILL_DIR / "research.md").is_file()
    assert (SKILL_DIR / "references" / "security_patterns.md").is_file()
    manifest = load_skill_manifest_safe()
    assert manifest.name == "research"
    assert manifest.tool_budget == 12


def test_build_research_user_message_excludes_skill_body(tmp_path):
    """Test Build Research User Message Excludes Skill Body for this module."""
    ticket = make_security_ticket("SEC-TEST-1")
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


def test_build_research_user_message_inlines_skill_pointer_not_body(tmp_path):
    """Security skill surfaces as a compact load_skill pointer, never the full body."""
    ticket = make_security_ticket("SEC-PTR-1")
    memory = InMemoryResearchMemory()
    message = build_research_user_message(
        ticket,
        str(tmp_path),
        None,
        ["search"],
        "",
        memory,
    )
    assert "## Skills System" in message
    assert "load_skill" in message
    assert "/security/injection.md" in message
    assert "(recommended)" in message
    assert "CWE-89" not in message
    assert "Negative Indicators" not in message
    assert "Positive Indicators" not in message


def test_build_research_user_message_lists_full_security_catalog_without_matches(tmp_path):
    """Even when no skill matches the ticket, list every loadable security skill."""
    from tests.helpers import make_bug_ticket

    ticket = make_bug_ticket("BUG-1")
    message = build_research_user_message(
        ticket,
        str(tmp_path),
        None,
        [],
        "",
        InMemoryResearchMemory(),
    )
    assert "## Skills System" in message
    assert "/security/cryptographic-failures.md" in message
    assert "/security/injection.md" in message
    assert "never invent paths from vulnerability_category" in message


def test_build_research_user_message_normalizes_repo_path(tmp_path):
    """Test Build Research User Message Normalizes Repo Path for this module."""
    ticket = make_security_ticket("SEC-NORM")
    memory = InMemoryResearchMemory()
    nested = tmp_path / "nested"
    nested.mkdir()
    (nested / "app.py").write_text("x = 1\n", encoding="utf-8")

    message = build_research_user_message(
        ticket,
        str(nested),
        None,
        [],
        "",
        memory,
    )

    resolved = str(nested.resolve())
    assert resolved in message
    assert '"exists": true' in message.lower() or '"exists": True' in message


@patch("deepagents.create_deep_agent")
def test_build_deep_agent_uses_research_only_stack(mock_create):
    """Test Build Deep Agent Uses Research Only Stack for this module."""
    from src.agents.research.deep_runner import _build_deep_agent
    from src.core.schemas import ResearchReport

    mock_create.return_value = MagicMock()
    llm = MagicMock()
    _build_deep_agent(llm, [], tool_budget=12)

    mock_create.assert_called_once()
    kwargs = mock_create.call_args.kwargs
    assert kwargs["subagents"] == []
    assert kwargs["response_format"] is ResearchReport
    assert "load_skill" in kwargs["system_prompt"].lower()
    assert "skills" not in kwargs
