"""Build Research agent tool set — repo read/ls/bash primitives + CursorGateway search."""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from src.agents.research.tools.load_skill import make_load_skill_tool
from src.agents.research.tools.cursor_search import make_cursor_search_tool
from src.agents.research.tools.repo_profile import make_repo_profile_tool
from src.agents.research.tools.web_search import web_search
from src.core.logging import get_logger
from src.core.tool_permissions import get_allowed_tools
from src.integrations.cursor.gateway import CursorGateway
from src.tools.registry import ToolRegistry

_log = get_logger("research_tools")

AGENT_SPECIFIC_TOOLS = frozenset({"load_skill", "cursor_search", "web_search", "repo_profile"})
# Repo-scoped L1 primitives the research agent may use for targeted file inspection.
PRIMITIVE_TOOLS = frozenset({"read", "ls", "bash", "grep", "find"})

# Max read/ls/bash calls per research run. Cursor-first locates files in one cheap
# cursor_search; the few surfaced files are inspected with primitives. This cap forces
# the agent to synthesize instead of looping reads forever (was a GraphRecursionError source).
PRIMITIVE_CALL_BUDGET = 8

# Short descriptions — full schemas stay in args_schema; cuts tool-definition tokens.
_PRIMITIVE_DESCRIPTIONS = {
    "read": "Read a repository file. Args: path, start_line, end_line.",
    "ls": "List a repository directory. Args: path, limit.",
    "bash": "Run a shell command in the repository. Args: command, timeout.",
}


def _make_primitive_lc_tools(
    repo_path: str, allowed: list[str], *, call_budget: int = PRIMITIVE_CALL_BUDGET
) -> list[StructuredTool]:
    """Wrap allowed L1 primitives (read/ls/bash/...) as repo-scoped LangChain tools.

    A shared per-run counter caps total primitive calls; once exhausted every primitive
    tool returns a STOP instruction so the agent emits its ResearchReport instead of
    looping (the recursion-limit failure mode).
    """
    registry = ToolRegistry(repo_path)
    # Shared mutable state across all primitive tools built in this run.
    state = {"calls": 0}
    lc_tools: list[StructuredTool] = []
    for name in allowed:
        if name not in PRIMITIVE_TOOLS:
            continue
        tool = registry.get(name)
        if tool is None:
            _log.error("primitive_missing", name=name)
            continue

        def _make_fn(t, tool_name):
            # Closure keeps the tool (and its registry) alive for the agent lifetime.
            """Make Fn for this module."""
            def fn(**kwargs):
                """Fn for this module."""
                if state["calls"] >= call_budget:
                    _log.warning(
                        "primitive_budget_exhausted",
                        tool=tool_name,
                        calls=state["calls"],
                        budget=call_budget,
                    )
                    return (
                        f"Primitive tool budget exhausted ({state['calls']}/{call_budget} "
                        "read/ls/bash calls). You have inspected enough files. Do NOT call "
                        "any more primitive tools — emit the final ResearchReport JSON now "
                        "using the evidence already gathered."
                    )
                state["calls"] += 1
                return t.execute(**kwargs).to_llm_string()

            return fn

        lc_tools.append(
            StructuredTool.from_function(
                func=_make_fn(tool, name),
                name=name,
                description=_PRIMITIVE_DESCRIPTIONS.get(name, tool.__class__.__doc__ or name),
                args_schema=tool.input_schema,
            )
        )
    return lc_tools


def build_research_tool_registry(
    repo_path: str,
    *,
    gateway: CursorGateway,
    thread_id: str = "research",
    hard_limit: int = 6,
    primitive_budget: int = PRIMITIVE_CALL_BUDGET,
    memory=None,
) -> tuple[None, list]:
    """Return (None, LangChain tools) for Research Deep Agent — no L1 ToolRegistry."""
    allowed = get_allowed_tools("research")
    lc_tools: list = []

    if "load_skill" in allowed:
        lc_tools.append(make_load_skill_tool())

    lc_tools.extend(
        _make_primitive_lc_tools(repo_path, allowed, call_budget=primitive_budget)
    )

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

    used = [n for n in allowed if n in AGENT_SPECIFIC_TOOLS or n in PRIMITIVE_TOOLS]
    _log.info("built", repo_path=repo_path, tools=used)
    return None, lc_tools
