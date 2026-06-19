"""Load agent tool permissions from YAML config."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

PERMISSIONS_PATH = Path(__file__).parent / "tool_permissions.yaml"


@lru_cache(maxsize=1)
def _load_permissions() -> dict[str, list[str]]:
    try:
        text = PERMISSIONS_PATH.read_text(encoding="utf-8")
    except OSError as e:
        raise ValueError(f"Failed to read tool permissions from {PERMISSIONS_PATH}: {e}") from e

    permissions: dict[str, list[str]] = {}
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.endswith(": []"):
            key = line[:-4].strip()
            permissions[key] = []
            current = None
            continue
        if line.endswith(":") and not line.startswith("-"):
            current = line[:-1].strip()
            permissions[current] = []
            continue
        if line.startswith("- ") and current:
            permissions[current].append(line[2:].strip())
    if not permissions:
        raise ValueError(f"No permissions parsed from {PERMISSIONS_PATH}")
    return permissions


def get_allowed_tools(agent_name: str) -> list[str]:
    """Return allowed tool names for an agent; raises if agent unknown."""
    permissions = _load_permissions()
    if agent_name not in permissions:
        raise ValueError(
            f"Unknown agent {agent_name!r} in tool_permissions.yaml. "
            f"Known: {sorted(permissions.keys())}"
        )
    return list(permissions[agent_name])
