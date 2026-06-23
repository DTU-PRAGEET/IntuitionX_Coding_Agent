"""Build Executor agent tool set — write, edit, bash only (no read/grep/find)."""

from __future__ import annotations

from src.core.logging import get_logger
from src.core.tool_permissions import get_allowed_tools
from src.tools.registry import ToolRegistry

_log = get_logger("executor_tools")

# Tools invoked directly by executor_node (not ToolRegistry primitives).
INTEGRATION_TOOLS = frozenset({"git_worktree", "mr_create"})


def build_executor_tools(repo_path: str) -> tuple[ToolRegistry, list[str]]:
    """Return (ToolRegistry, allowed tool names) for Executor."""
    allowed = get_allowed_tools("executor")
    registry = ToolRegistry(repo_path)
    for name in allowed:
        if name in INTEGRATION_TOOLS:
            continue
        if registry.get(name) is None:
            registry.dispose()
            raise RuntimeError(f"Executor tool {name!r} missing from ToolRegistry")
    _log.info("built", repo_path=repo_path, tools=allowed)
    return registry, allowed
