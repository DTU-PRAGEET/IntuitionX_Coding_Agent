"""Atomic edit tool — targeted search/replace patch."""

from __future__ import annotations

from src.core.schemas import EditInput, ToolResult
from src.tools.base import ToolDefinition, audit_tool


class EditTool(ToolDefinition):
    """Apply a patch edit. old_string must appear exactly once."""

    name = "edit"
    input_schema = EditInput

    @audit_tool
    def execute(self, path: str, old_string: str, new_string: str) -> ToolResult:
        try:
            resolved = self.resolve_path(path)
        except ValueError as e:
            return self._wrap_output(str(e), exit_code=1, is_error=True)
        if not resolved.exists():
            return self._wrap_output(f"Error: file not found: {path}", exit_code=1, is_error=True)
        try:
            content = resolved.read_text(encoding="utf-8")
        except OSError as e:
            raise OSError(f"edit read failed for path={path!r}: {e}") from e
        count = content.count(old_string)
        if count == 0:
            return self._wrap_output(
                f"Error: old_string not found in {path}", exit_code=1, is_error=True
            )
        if count > 1:
            return self._wrap_output(
                f"Error: old_string appears {count} times in {path}; must be unique",
                exit_code=1,
                is_error=True,
            )
        try:
            resolved.write_text(content.replace(old_string, new_string, 1), encoding="utf-8")
        except OSError as e:
            raise OSError(f"edit write failed for path={path!r}: {e}") from e
        return self._wrap_output(f"Successfully patched {path}")
