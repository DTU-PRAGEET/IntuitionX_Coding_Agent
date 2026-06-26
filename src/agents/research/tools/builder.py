"""Build Research agent tool set — CursorGateway search + agent-specific tools."""

from __future__ import annotations

from src.agents.research.tools.cursor_search import make_cursor_search_tool
from src.agents.research.tools.langchain_tools import make_repo_profile_tool
from src.agents.research.tools.web_search import web_search
from src.core.logging import get_logger
from src.core.tool_permissions import get_allowed_tools
from src.integrations.cursor.gateway import CursorGateway

_log = get_logger("research_tools")

AGENT_SPECIFIC_TOOLS = frozenset({"cursor_search", "web_search", "repo_profile"})


def build_research_tool_registry(
    repo_path: str,
    *,
    gateway: CursorGateway,
    thread_id: str = "research",
    hard_limit: int = 6,
    memory=None,
) -> tuple[None, list]:
    """Return (None, LangChain tools) for Research Deep Agent — no L1 ToolRegistry."""
    allowed = get_allowed_tools("research")
    lc_tools = []

    if "cursor_search" in allowed:
        lc_tools.append(
            make_cursor_search_tool(
                gateway,
                thread_id=thread_id,
                hard_limit=hard_limit,
                memory=memory,
            )
        )
    if "web_search" in allowed:
        lc_tools.append(web_search)
    if "repo_profile" in allowed:
        lc_tools.append(make_repo_profile_tool(repo_path))

    agent_used = [n for n in allowed if n in AGENT_SPECIFIC_TOOLS]
    _log.info("built", repo_path=repo_path, agent_tools=agent_used)
    return None, lc_tools
