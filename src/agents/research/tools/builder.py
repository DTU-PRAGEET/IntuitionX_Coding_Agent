"""Build Research agent tool set from L1 primitives + agent-specific tools."""

from __future__ import annotations

from src.agents.research.tools.langchain_tools import make_repo_profile_tool
from src.agents.research.tools.web_search import web_search
from src.core.logging import get_logger
from src.core.tool_permissions import get_allowed_tools
from src.tools.registry import PRIMITIVE_TOOL_NAMES, ToolRegistry

_log = get_logger("research_tools")

AGENT_SPECIFIC_TOOLS = frozenset({"web_search", "repo_profile"})


def build_research_tool_registry(repo_path: str) -> tuple[ToolRegistry, list]:
    """Return (ToolRegistry, LangChain tools) for Research Deep Agent."""
    allowed = get_allowed_tools("research")
    registry = ToolRegistry(repo_path)
    lc_tools = []

    for name in allowed:
        if name in AGENT_SPECIFIC_TOOLS:
            continue
        if name not in PRIMITIVE_TOOL_NAMES:
            raise RuntimeError(f"Research tool {name!r} is not a known primitive or agent tool")
        tool = registry.get(name)
        if tool is None:
            registry.dispose()
            raise RuntimeError(f"Research tool {name!r} missing from ToolRegistry")
        schema = tool.input_schema

        def _make_fn(t=tool):
            def fn(**kwargs):
                result = t.execute(**kwargs)
                return result.to_llm_string()

            return fn

        from langchain_core.tools import StructuredTool

        lc_tools.append(
            StructuredTool.from_function(
                func=_make_fn(),
                name=name,
                description=tool.__class__.__doc__ or name,
                args_schema=schema,
            )
        )

    if "web_search" in allowed:
        lc_tools.append(web_search)
    if "repo_profile" in allowed:
        lc_tools.append(make_repo_profile_tool(repo_path))

    l1_used = [n for n in allowed if n in PRIMITIVE_TOOL_NAMES]
    agent_used = [n for n in allowed if n in AGENT_SPECIFIC_TOOLS]
    _log.info("built", repo_path=repo_path, l1_tools=l1_used, agent_tools=agent_used)
    return registry, lc_tools
