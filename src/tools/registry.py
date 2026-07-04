"""Central tool registry — one instance per workspace session."""

from __future__ import annotations

from src.core.logging import get_logger
from src.core.schemas import ToolResult
from src.tools.base import ToolDefinition
from src.tools.bash_tool import BashTool
from src.tools.edit_tool import EditTool
from src.tools.find_tool import FindTool
from src.tools.grep_tool import GrepTool
from src.tools.ls_tool import LsTool
from src.tools.read_tool import ReadTool
from src.tools.write_tool import WriteTool

_log = get_logger("tool_registry")

PRIMITIVE_TOOL_NAMES = ("read", "write", "edit", "bash", "find", "grep", "ls")


class ToolRegistry:
    """Registry of hardened L1 tools scoped to a workspace cwd."""

    def __init__(self, cwd: str):
        """Initialize ToolRegistry with the provided arguments."""
        self.cwd = cwd
        self._tools: dict[str, ToolDefinition] = {
            "read": ReadTool(cwd),
            "write": WriteTool(cwd),
            "edit": EditTool(cwd),
            "bash": BashTool(cwd),
            "find": FindTool(cwd),
            "grep": GrepTool(cwd),
            "ls": LsTool(cwd),
        }
        _log.info("initialized", cwd=cwd, tools=list(self._tools.keys()))

    def get(self, name: str) -> ToolDefinition | None:
        """Get for ToolRegistry."""
        return self._tools.get(name)

    def list_names(self) -> list[str]:
        """List Names for ToolRegistry."""
        return list(self._tools.keys())

    def execute(self, name: str, **kwargs) -> ToolResult:
        """Execute for ToolRegistry."""
        tool = self._tools.get(name)
        if tool is None:
            available = ", ".join(self.list_names())
            msg = f"Unknown tool {name!r}. Available: {available}"
            _log.error("unknown_tool", name=name, available=available)
            return ToolResult(output=msg, exit_code=1, is_error=True, tool_name=name)
        return tool.execute(**kwargs)

    def to_langchain_tools(self) -> list:
        """Export registry tools for LangChain ReAct agents."""
        from langchain_core.tools import StructuredTool

        lc_tools = []
        for name, tool in self._tools.items():
            schema = tool.input_schema

            def _make_fn(t: ToolDefinition):
                """Make Fn for ToolRegistry."""
                def fn(**kwargs):
                    """Fn for ToolRegistry."""
                    result = t.execute(**kwargs)
                    return result.to_llm_string()

                return fn

            lc_tools.append(
                StructuredTool.from_function(
                    func=_make_fn(tool),
                    name=name,
                    description=tool.__class__.__doc__ or name,
                    args_schema=schema,
                )
            )
        return lc_tools

    def dispose(self) -> None:
        """Release session resources."""
        self._tools.clear()
        _log.info("disposed", cwd=self.cwd)
