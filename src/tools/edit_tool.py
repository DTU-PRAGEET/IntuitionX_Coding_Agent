"""Atomic edit tool — normalized exact/fuzzy search/replace patch."""

from __future__ import annotations

import difflib
from typing import Any

from src.core.schemas import EditInput, ToolResult
from src.tools.base import ToolDefinition, audit_tool
from src.tools.edit_args import prepare_edit_arguments
from src.tools.edit_diff import (
    EditError,
    apply_edits_to_normalized_content,
    detect_line_ending,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)


class EditTool(ToolDefinition):
    """Edit a file using exact text replacement.

    - old_string must appear exactly once (after normalization).
    - Copy old_string verbatim from read output; do not reconstruct from memory.
    - Include enough surrounding lines to make it unique.
    - For multiple locations, pass edits=[{old_string, new_string}, ...].
    """

    name = "edit"
    input_schema = EditInput

    @audit_tool
    def execute(self, path: str, **kwargs: Any) -> ToolResult:
        try:
            file_path, edits = prepare_edit_arguments(path=path, **kwargs)
            resolved = self.resolve_path(file_path)
        except (ValueError, EditError) as e:
            return self._wrap_output(str(e), exit_code=1, is_error=True)

        if not resolved.exists():
            return self._wrap_output(f"Error: file not found: {file_path}", exit_code=1, is_error=True)
        try:
            raw = resolved.read_bytes().decode("utf-8")
        except OSError as e:
            raise OSError(f"edit read failed for path={file_path!r}: {e}") from e

        bom, content = strip_bom(raw)
        original_ending = detect_line_ending(content)
        normalized = normalize_to_lf(content)

        try:
            base, new_content = apply_edits_to_normalized_content(normalized, edits, file_path)
        except EditError as e:
            return self._wrap_output(str(e), exit_code=1, is_error=True)

        restored = restore_line_endings(new_content, original_ending)
        final = bom + restored

        try:
            resolved.write_bytes(final.encode("utf-8"))
        except OSError as e:
            raise OSError(f"edit write failed for path={file_path!r}: {e}") from e

        diff_lines = list(
            difflib.unified_diff(
                base.splitlines(keepends=True),
                new_content.splitlines(keepends=True),
                fromfile=f"a/{file_path}",
                tofile=f"b/{file_path}",
                n=2,
            )
        )
        diff_snippet = "".join(diff_lines[:20])
        blocks = len(edits)
        output = f"Successfully replaced {blocks} block(s) in {file_path}."
        if diff_snippet:
            output = f"{output}\n\n--- diff ---\n{diff_snippet}"
        return self._wrap_output(output)
