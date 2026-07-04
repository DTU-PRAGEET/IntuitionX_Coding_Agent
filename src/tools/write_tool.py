"""Atomic write tool."""

from __future__ import annotations

from src.core.schemas import ToolResult, WriteInput
from src.tools.base import ToolDefinition, audit_tool


class WriteTool(ToolDefinition):
    """Write or overwrite a file in the workspace."""

    name = "write"
    input_schema = WriteInput

    @audit_tool
    def execute(self, path: str, content: str) -> ToolResult:
        """Execute for WriteTool."""
        try:
            resolved = self.resolve_path(path)
        except ValueError as e:
            return self._wrap_output(str(e), exit_code=1, is_error=True)
        try:
            resolved.parent.mkdir(parents=True, exist_ok=True)
            resolved.write_text(content, encoding="utf-8")
        except OSError as e:
            raise OSError(f"write failed for path={path!r}: {e}") from e
        return self._wrap_output(f"Wrote {len(content)} bytes to {path}")
