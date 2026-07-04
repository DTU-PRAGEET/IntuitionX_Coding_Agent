"""Build Executor agent tool set — read/bash only; edits via CursorGateway."""

from __future__ import annotations

from src.core.logging import get_logger
from src.core.schemas import ToolResult
from src.core.tool_permissions import get_allowed_tools
from src.tools.registry import ToolRegistry

_log = get_logger("executor_tools")

# Tools invoked directly by executor_node (not ToolRegistry primitives).
INTEGRATION_TOOLS = frozenset({"git_worktree", "mr_create"})


class RestrictedToolRegistry(ToolRegistry):
    """ToolRegistry that denies execute() for tools outside the executor allowlist."""

    def __init__(self, cwd: str, allowed: frozenset[str]):
        """Initialize RestrictedToolRegistry with the provided arguments."""
        super().__init__(cwd)
        self._allowed = allowed

    def execute(self, name: str, **kwargs) -> ToolResult:
        """Execute for RestrictedToolRegistry."""
        if name not in self._allowed:
            msg = (
                f"Tool {name!r} is not allowed for the Executor agent. "
                f"File mutations must go through CursorGateway.edit(). "
                f"Allowed: {sorted(self._allowed)}"
            )
            _log.error("tool_denied", name=name, allowed=sorted(self._allowed))
            return ToolResult(output=msg, exit_code=1, is_error=True, tool_name=name)
        return super().execute(name, **kwargs)


def build_executor_tools(repo_path: str) -> tuple[RestrictedToolRegistry, list[str]]:
    """Return (RestrictedToolRegistry, allowed tool names) for Executor."""
    allowed_list = get_allowed_tools("executor")
    primitive_allowed = frozenset(
        name for name in allowed_list if name not in INTEGRATION_TOOLS
    )
    registry = RestrictedToolRegistry(repo_path, primitive_allowed)
    for name in allowed_list:
        if name in INTEGRATION_TOOLS:
            continue
        if registry.get(name) is None:
            registry.dispose()
            raise RuntimeError(f"Executor tool {name!r} missing from ToolRegistry")
    _log.info("built", repo_path=repo_path, tools=allowed_list)
    return registry, allowed_list
