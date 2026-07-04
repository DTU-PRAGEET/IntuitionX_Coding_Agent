"""Tests for research deepagents token optimizations."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import ToolMessage

from src.agents.research.deep_runner import (
    _build_deep_agent,
    build_research_user_message,
)
from src.agents.research.middleware import (
    RESEARCH_ALLOWED_TOOLS,
    RESEARCH_CORE_TOOLS,
    RESEARCH_EXCLUDED_DEEPAGENT_TOOLS,
    ProgressiveSkillMiddleware,
    ResearchToolFilterMiddleware,
    _cursor_search_completed,
    _visible_research_tools,
)
from src.agents.research.run_context import ResearchRunContext
from src.agents.research.tools.builder import build_research_tool_registry
from src.agents.research.tools.load_skill import make_load_skill_tool
from src.agents.research.memory import InMemoryResearchMemory
from src.integrations.cursor.gateway import get_cursor_gateway
from tests.helpers import make_security_ticket

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_research_excluded_tools_cover_deepagents_scaffolding():
    """Test Research Excluded Tools Cover Deepagents Scaffolding for this module."""
    assert "task" in RESEARCH_EXCLUDED_DEEPAGENT_TOOLS
    assert "write_todos" in RESEARCH_EXCLUDED_DEEPAGENT_TOOLS
    assert "read_file" in RESEARCH_EXCLUDED_DEEPAGENT_TOOLS
    assert "write_file" in RESEARCH_EXCLUDED_DEEPAGENT_TOOLS


def test_progressive_tool_disclosure_starts_core_only():
    """Test Progressive Tool Disclosure Starts Core Only for this module."""
    assert _visible_research_tools([], None) == RESEARCH_CORE_TOOLS


def test_progressive_tool_disclosure_hides_load_skill_after_load():
    """Test Progressive Tool Disclosure Hides Load Skill After Load for this module."""
    ctx = ResearchRunContext()
    ctx.loaded_skills["research"] = "workflow body"
    visible = _visible_research_tools([], ctx)
    assert "load_skill" not in visible
    assert "cursor_search" in visible


def test_progressive_tool_disclosure_unlocks_after_cursor_search():
    """Test Progressive Tool Disclosure Unlocks After Cursor Search for this module."""
    from langchain_core.messages import AIMessage, ToolMessage

    messages = [
        AIMessage(
            content="",
            tool_calls=[{"id": "c1", "name": "cursor_search", "args": {"query": "auth"}}],
        ),
        ToolMessage(content='{"hits":[]}', tool_call_id="c1", name="cursor_search"),
    ]
    assert _cursor_search_completed(messages)
    visible = _visible_research_tools(messages, None)
    assert visible == RESEARCH_ALLOWED_TOOLS


def test_build_research_user_message_has_load_skill_catalog(tmp_path):
    """Test Build Research User Message Has Load Skill Catalog for this module."""
    ticket = make_security_ticket("SEC-CAT-1")
    message = build_research_user_message(
        ticket,
        str(tmp_path),
        None,
        ["search"],
        "",
        InMemoryResearchMemory(),
    )
    assert "## Skills System" in message
    assert "load_skill" in message
    assert "`research`" in message
    assert "/security/injection.md" in message
    assert "CWE-89" not in message


def test_load_skill_tool_resolves_research_and_security():
    """Test Load Skill Tool Resolves Research And Security for this module."""
    tool = make_load_skill_tool()
    research_body = tool.invoke({"skill": "research"})
    injection_body = tool.invoke({"skill": "injection"})
    assert "Research Agent" in research_body or "research" in research_body.lower()
    assert len(injection_body) > 100


def test_load_skill_tool_resolves_security_rce_alias():
    """Stale vulnerability_category values like security/rce map to injection."""
    tool = make_load_skill_tool()
    body = tool.invoke({"skill": "security/rce"})
    assert len(body) > 100
    assert "load_skill error" not in body.lower()


def test_progressive_skill_middleware_compacts_tool_message_and_injects_system():
    """Test Progressive Skill Middleware Compacts Tool Message And Injects System for this module."""
    ctx = ResearchRunContext()
    mw = ProgressiveSkillMiddleware(ctx)
    request = MagicMock()
    request.tool_call = {"name": "load_skill", "args": {"skill": "injection"}}
    handler = MagicMock(
        return_value=ToolMessage(content="X" * 5000, tool_call_id="t1", name="load_skill")
    )

    result = mw.wrap_tool_call(request, handler)
    assert len(result.content) < 200
    assert "injection" in ctx.loaded_skills
    assert len(ctx.loaded_skills["injection"]) == 5000


@patch("deepagents.create_deep_agent")
def test_build_deep_agent_research_only_profile(mock_create):
    """Test Build Deep Agent Research Only Profile for this module."""
    from src.core.schemas import ResearchReport

    mock_create.return_value = MagicMock()
    llm = MagicMock()
    _build_deep_agent(llm, [], tool_budget=12)

    mock_create.assert_called_once()
    kwargs = mock_create.call_args.kwargs
    assert kwargs["subagents"] == []
    assert kwargs["response_format"] is ResearchReport
    assert len(kwargs["middleware"]) == 2
    assert isinstance(kwargs["middleware"][0], ProgressiveSkillMiddleware)
    assert isinstance(kwargs["middleware"][1], ResearchToolFilterMiddleware)
    assert "skills" not in kwargs
    assert "load_skill" in kwargs["system_prompt"].lower()
    assert "Security guidance" not in kwargs["system_prompt"]


@pytest.mark.skipif(
    not Path(REPO_ROOT / ".venv/bin/python").exists(),
    reason="deepagents only available in project venv",
)
def test_model_visible_tools_are_trimmed_and_cheap():
    """Middleware hides deepagents scaffolding from the model even if the executor binds them."""
    pytest.importorskip("deepagents")
    try:
        import tiktoken
        from langchain_core.utils.function_calling import convert_to_openai_tool
        from src.core.llm import TaskComplexity, get_llm
    except ImportError:
        pytest.skip("tiktoken or llm deps unavailable")

    repo = str(REPO_ROOT)
    gw = get_cursor_gateway(repo, dry_run=True)
    _, tools = build_research_tool_registry(repo, gateway=gw, thread_id="tok-test", hard_limit=2)
    llm = get_llm(TaskComplexity.HIGH)
    agent = _build_deep_agent(llm, tools, 12)

    node = agent.nodes["tools"]
    all_tools: dict[str, object] = {}

    def walk(o, depth=0):
        """Walk for this module."""
        if depth > 8:
            return
        from langchain_core.tools import BaseTool

        if isinstance(o, BaseTool):
            all_tools[o.name] = o
            return
        for attr in ("runnable", "bound", "steps", "tools_by_name", "func", "node", "_tools"):
            if hasattr(o, attr):
                walk(getattr(o, attr), depth + 1)
        if isinstance(o, dict):
            for vv in o.values():
                walk(vv, depth + 1)
        if isinstance(o, (list, tuple)):
            for vv in o:
                walk(vv, depth + 1)

    walk(node)

    visible_names = _visible_research_tools([], None)
    model_tools = [all_tools[n] for n in visible_names if n in all_tools]

    assert "task" not in visible_names
    assert "write_todos" not in visible_names
    assert "read_file" not in visible_names
    assert "load_skill" in visible_names
    assert {t.name for t in model_tools} == visible_names

    enc = tiktoken.get_encoding("o200k_base")
    schema_tokens = sum(
        len(enc.encode(json.dumps(convert_to_openai_tool(t)))) for t in model_tools
    )
    # Core phase: load_skill + cursor_search + read + ls + bash (~500-900 tokens).
    assert schema_tokens < 1000, f"core tool schemas still too large: {schema_tokens}"
