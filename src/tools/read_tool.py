"""Atomic read tool — bounded line reads."""

from __future__ import annotations

from typing import Optional

from src.core.config import settings
from src.core.schemas import ReadInput, ToolResult
from src.tools.base import ToolDefinition, audit_tool
from src.tools.truncate import truncate_head


class ReadTool(ToolDefinition):
    """Read file contents with optional line range. Max 500 lines per call."""

    name = "read"
    input_schema = ReadInput

    @audit_tool
    def execute(
        self,
        path: str,
        start_line: int = 1,
        end_line: Optional[int] = None,
    ) -> ToolResult:
        try:
            resolved = self.resolve_path(path)
        except ValueError as e:
            return self._wrap_output(str(e), exit_code=1, is_error=True)
        if not resolved.exists():
            return self._wrap_output(f"Error: file not found: {path}", exit_code=1, is_error=True)
        try:
            raw = resolved.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            raise OSError(f"read failed for path={path!r}: {e}") from e
        lines = raw.splitlines()
        max_lines = settings.max_lines_per_read
        end = end_line or min(start_line + max_lines - 1, len(lines))
        end = min(end, start_line + max_lines - 1)
        selected = lines[max(0, start_line - 1) : end]
        numbered = [f"{i + start_line}: {line}" for i, line in enumerate(selected)]
        body = "\n".join(numbered) if numbered else "(empty file)"
        truncated = truncate_head(body, max_lines=max_lines, max_bytes=50 * 1024)
        return self._wrap_output(truncated.content)
