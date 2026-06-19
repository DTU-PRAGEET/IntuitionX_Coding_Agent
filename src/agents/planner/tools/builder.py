"""Build Planner agent tool set — read, grep, find only (no write/edit/bash)."""

from __future__ import annotations

from src.core.logging import get_logger
from src.core.tool_permissions import get_allowed_tools
from src.tools.registry import ToolRegistry

_log = get_logger("planner_tools")


def build_planner_tools(repo_path: str) -> tuple[ToolRegistry, list[str]]:
    """Return (ToolRegistry, allowed tool names) for Planner verification."""
    allowed = get_allowed_tools("planner")
    registry = ToolRegistry(repo_path)
    for name in allowed:
        if registry.get(name) is None:
            registry.dispose()
            raise RuntimeError(f"Planner tool {name!r} missing from ToolRegistry")
    _log.info("built", repo_path=repo_path, tools=allowed)
    return registry, allowed
