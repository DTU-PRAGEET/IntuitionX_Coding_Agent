"""List directory tool — native listing with entry limit."""

from __future__ import annotations

import os

from src.core.schemas import LsInput, ToolResult
from src.tools.base import ToolDefinition, audit_tool

DEFAULT_LIMIT = 500


class LsTool(ToolDefinition):
    """List directory entries scoped to workspace cwd."""

    name = "ls"
    input_schema = LsInput

    @audit_tool
    def execute(self, path: str = ".", limit: int = DEFAULT_LIMIT) -> ToolResult:
        """Execute for LsTool."""
        try:
            resolved = self.resolve_path(path)
        except ValueError as e:
            return self._wrap_output(str(e), exit_code=1, is_error=True)
        if not resolved.exists():
            return self._wrap_output(f"Error: path not found: {path}", exit_code=1, is_error=True)
        if not resolved.is_dir():
            return self._wrap_output(f"Error: not a directory: {path}", exit_code=1, is_error=True)
        try:
            entries = sorted(resolved.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
        except OSError as e:
            raise OSError(f"ls failed for path={path!r}: {e}") from e

        lines: list[str] = []
        truncated = False
        for entry in entries:
            rel = os.path.relpath(entry, str(resolved))
            kind = "dir" if entry.is_dir() else "file"
            lines.append(f"{kind}\t{rel}")
            if len(lines) >= limit:
                truncated = True
                lines.append(f"[Truncated: {limit} entries limit reached]")
                break

        body = "\n".join(lines) if lines else "(empty directory)"
        if truncated:
            body += f"\n[entry_limit={limit}]"
        return self._wrap_output(body)
