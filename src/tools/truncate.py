"""Shared truncation utilities — line and byte limits (Pi-style)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

DEFAULT_MAX_LINES = 2000
DEFAULT_MAX_BYTES = 50 * 1024
GREP_MAX_LINE_LENGTH = 500


@dataclass(frozen=True)
class TruncationResult:
    content: str
    truncated: bool
    truncated_by: Literal["lines", "bytes"] | None
    total_lines: int
    total_bytes: int
    output_lines: int
    output_bytes: int
    max_lines: int
    max_bytes: int


def format_size(num_bytes: int) -> str:
    if num_bytes < 1024:
        return f"{num_bytes}B"
    if num_bytes < 1024 * 1024:
        return f"{num_bytes / 1024:.1f}KB"
    return f"{num_bytes / (1024 * 1024):.1f}MB"


def _split_lines(content: str) -> list[str]:
    if not content:
        return []
    lines = content.split("\n")
    if content.endswith("\n"):
        lines.pop()
    return lines


def truncate_head(
    content: str,
    max_lines: int = DEFAULT_MAX_LINES,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> TruncationResult:
    """Keep first N lines/bytes — never return partial lines."""
    lines = _split_lines(content)
    total_bytes = len(content.encode("utf-8"))
    total_lines = len(lines) if content else (1 if content == "" else 0)

    if not lines and content == "":
        return TruncationResult(
            content="",
            truncated=False,
            truncated_by=None,
            total_lines=0,
            total_bytes=0,
            output_lines=0,
            output_bytes=0,
            max_lines=max_lines,
            max_bytes=max_bytes,
        )

    kept: list[str] = []
    byte_count = 0
    truncated_by: Literal["lines", "bytes"] | None = None

    for i, line in enumerate(lines):
        if i >= max_lines:
            truncated_by = "lines"
            break
        line_bytes = len((line + "\n").encode("utf-8"))
        if byte_count + line_bytes > max_bytes and not kept:
            truncated_by = "bytes"
            break
        if byte_count + line_bytes > max_bytes:
            truncated_by = "bytes"
            break
        kept.append(line)
        byte_count += line_bytes

    result_content = "\n".join(kept)
    if kept and len(lines) > len(kept):
        truncated_by = truncated_by or "lines"
    truncated = truncated_by is not None
    if truncated and truncated_by:
        note = f"[Truncated by {truncated_by}: showing {len(kept)}/{len(lines)} lines, {format_size(byte_count)}/{format_size(total_bytes)}]"
        result_content = result_content + "\n" + note if result_content else note

    return TruncationResult(
        content=result_content,
        truncated=truncated,
        truncated_by=truncated_by,
        total_lines=len(lines),
        total_bytes=total_bytes,
        output_lines=len(kept),
        output_bytes=byte_count,
        max_lines=max_lines,
        max_bytes=max_bytes,
    )


def truncate_line(line: str, max_chars: int = GREP_MAX_LINE_LENGTH) -> str:
    """Truncate a single line for grep output."""
    if len(line) <= max_chars:
        return line
    return line[: max_chars - 3] + "..."
