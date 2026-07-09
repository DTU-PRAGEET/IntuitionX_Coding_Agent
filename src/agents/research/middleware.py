"""Research-only deepagents middleware — trim tools and progressive skill loading."""

from __future__ import annotations

from langchain.agents.middleware.types import AgentMiddleware
from langchain_core.messages import AIMessage, ToolMessage

from src.agents.research.run_context import ResearchRunContext
from src.core.logging import get_logger

_log = get_logger("research_middleware")

# Repo-scoped investigation tools + progressive skill loader.
RESEARCH_ALLOWED_TOOLS = frozenset(
    {"load_skill", "cursor_search", "read", "ls", "bash", "web_search", "repo_profile"}
)

# Phase 1: load skill + locate and inspect. Optional tools unlock after cursor_search.
RESEARCH_CORE_TOOLS = frozenset({"load_skill", "cursor_search", "read", "ls", "bash"})
RESEARCH_EXTENDED_TOOLS = frozenset({"web_search", "repo_profile"})

# Deepagents built-ins to hide from the model (skills backend or unused scaffolding).
RESEARCH_EXCLUDED_DEEPAGENT_TOOLS = frozenset(
    {
        "ls",
        "read_file",
        "write_file",
        "edit_file",
        "glob",
        "grep",
        "execute",
        "write_todos",
        "task",
    }
)

_LOAD_SKILL_ACK = "Skill loaded — guidance is now in your system instructions."
_LOAD_SKILL_ALREADY = "Skill already loaded — use the guidance in your system instructions."
_LOAD_SKILL_ERROR_PREFIX = "load_skill error:"


def _is_load_skill_error(content: str) -> bool:
    """True when load_skill returned a recoverable error string instead of a body."""
    return str(content).lstrip().startswith(_LOAD_SKILL_ERROR_PREFIX)


def _tool_name(tool: object) -> str | None:
    """Tool Name for this module."""
    if isinstance(tool, dict):
        name = tool.get("name")
        return name if isinstance(name, str) else None
    name = getattr(tool, "name", None)
    return name if isinstance(name, str) else None


def _cursor_search_completed(messages: list) -> bool:
    """True once cursor_search has returned at least one ToolMessage."""
    pending: set[str] = set()
    for msg in messages or []:
        if isinstance(msg, AIMessage):
            for call in getattr(msg, "tool_calls", None) or []:
                if call.get("name") == "cursor_search":
                    pending.add(str(call.get("id") or ""))
        elif isinstance(msg, ToolMessage):
            if msg.name == "cursor_search" or msg.tool_call_id in pending:
                return True
    return False


def _visible_research_tools(messages: list, run_context: ResearchRunContext | None) -> frozenset[str]:
    """Visible Research Tools for this module."""
    visible = set(RESEARCH_CORE_TOOLS)
    if _cursor_search_completed(messages):
        visible |= RESEARCH_EXTENDED_TOOLS
    return frozenset(visible)


def _append_text_to_system_message(system_message, text: str):
    """Append Text To System Message for this module."""
    from langchain_core.messages import SystemMessage

    from deepagents.middleware._utils import append_to_system_message

    if system_message is None:
        return SystemMessage(content=text)
    return append_to_system_message(system_message, text)


def _compact_load_skill_messages(messages: list) -> list:
    """Replace verbose load_skill ToolMessages with a short ack for history replay."""
    compacted: list = []
    for msg in messages:
        if isinstance(msg, ToolMessage) and msg.name == "load_skill":
            if len(str(msg.content)) > len(_LOAD_SKILL_ACK) + 20:
                compacted.append(
                    ToolMessage(
                        content=_LOAD_SKILL_ACK,
                        tool_call_id=msg.tool_call_id,
                        name=msg.name,
                    )
                )
                continue
        compacted.append(msg)
    return compacted


class ResearchToolFilterMiddleware(AgentMiddleware):
    """Keep only research investigation tools visible to the model."""

    def __init__(self, run_context: ResearchRunContext | None = None) -> None:
        """Initialize ResearchToolFilterMiddleware with the provided arguments."""
        self._run_context = run_context or ResearchRunContext()

    def wrap_model_call(self, request, handler):
        """Wrap Model Call for ResearchToolFilterMiddleware."""
        allowed = _visible_research_tools(request.messages, self._run_context)
        filtered = [t for t in request.tools if _tool_name(t) in allowed]
        return handler(request.override(tools=filtered))

    async def awrap_model_call(self, request, handler):
        """Awrap Model Call for ResearchToolFilterMiddleware."""
        allowed = _visible_research_tools(request.messages, self._run_context)
        filtered = [t for t in request.tools if _tool_name(t) in allowed]
        return await handler(request.override(tools=filtered))


class ProgressiveSkillMiddleware(AgentMiddleware):
    """Intercept load_skill: inject body into system prompt, keep history compact."""

    def __init__(self, run_context: ResearchRunContext | None = None) -> None:
        """Initialize ProgressiveSkillMiddleware with the provided arguments."""
        self._run_context = run_context or ResearchRunContext()

    def wrap_tool_call(self, request, handler):
        """Wrap Tool Call for ProgressiveSkillMiddleware."""
        if request.tool_call.get("name") != "load_skill":
            return handler(request)

        skill_key = str(request.tool_call.get("args", {}).get("skill", "skill")).strip()
        if skill_key in self._run_context.loaded_skills:
            return ToolMessage(
                content=_LOAD_SKILL_ALREADY,
                tool_call_id=request.tool_call.get("id") or "",
                name="load_skill",
            )

        result = handler(request)
        if not isinstance(result, ToolMessage):
            return result

        skill_key = str(request.tool_call.get("args", {}).get("skill", "skill")).strip()
        body = str(result.content)
        if body.strip() and not _is_load_skill_error(body):
            self._run_context.loaded_skills[skill_key] = body
            _log.info("skill_injected", skill=skill_key, chars=len(body))
            return ToolMessage(
                content=_LOAD_SKILL_ACK,
                tool_call_id=result.tool_call_id,
                name="load_skill",
            )

        return ToolMessage(
            content=body,
            tool_call_id=result.tool_call_id,
            name="load_skill",
        )

    async def awrap_tool_call(self, request, handler):
        """Awrap Tool Call for ProgressiveSkillMiddleware."""
        if request.tool_call.get("name") != "load_skill":
            return await handler(request)

        skill_key = str(request.tool_call.get("args", {}).get("skill", "skill")).strip()
        if skill_key in self._run_context.loaded_skills:
            return ToolMessage(
                content=_LOAD_SKILL_ALREADY,
                tool_call_id=request.tool_call.get("id") or "",
                name="load_skill",
            )

        result = await handler(request)
        if not isinstance(result, ToolMessage):
            return result

        skill_key = str(request.tool_call.get("args", {}).get("skill", "skill")).strip()
        body = str(result.content)
        if body.strip() and not _is_load_skill_error(body):
            self._run_context.loaded_skills[skill_key] = body
            _log.info("skill_injected", skill=skill_key, chars=len(body))
            return ToolMessage(
                content=_LOAD_SKILL_ACK,
                tool_call_id=result.tool_call_id,
                name="load_skill",
            )

        return ToolMessage(
            content=body,
            tool_call_id=result.tool_call_id,
            name="load_skill",
        )

    def wrap_model_call(self, request, handler):
        """Wrap Model Call for ProgressiveSkillMiddleware."""
        messages = _compact_load_skill_messages(request.messages)
        system_message = request.system_message

        if self._run_context.loaded_skills:
            skill_blocks = [
                f"## Loaded skill: {name}\n{body}"
                for name, body in self._run_context.loaded_skills.items()
            ]
            system_message = _append_text_to_system_message(
                system_message,
                "\n\n".join(skill_blocks),
            )

        overrides = {"messages": messages}
        if system_message is not request.system_message:
            overrides["system_message"] = system_message
        return handler(request.override(**overrides))

    async def awrap_model_call(self, request, handler):
        """Awrap Model Call for ProgressiveSkillMiddleware."""
        messages = _compact_load_skill_messages(request.messages)
        system_message = request.system_message

        if self._run_context.loaded_skills:
            skill_blocks = [
                f"## Loaded skill: {name}\n{body}"
                for name, body in self._run_context.loaded_skills.items()
            ]
            system_message = _append_text_to_system_message(
                system_message,
                "\n\n".join(skill_blocks),
            )

        overrides = {"messages": messages}
        if system_message is not request.system_message:
            overrides["system_message"] = system_message
        return await handler(request.override(**overrides))
