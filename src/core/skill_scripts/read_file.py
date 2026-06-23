#!/usr/bin/env python3
"""
SCRIPT: read_file.py
PURPOSE: Safe file reader with optional line ranges for agent edit grounding.
USAGE:
    python read_file.py --repo <repo_path> --file <path/to/file> [--lines N:M]

STDOUT (on success):
    Line-numbered file content (verbatim, no truncation of selected range)

STDOUT (on failure):
    READ_FAIL: FILE_NOT_FOUND <file>
    READ_FAIL: INVALID_LINES <reason>

EXIT CODES:
    0 = success
    1 = validation error
    2 = file not found
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from src.tools.registry import ToolRegistry


def _parse_lines(spec: str | None) -> tuple[int, int | None]:
    if not spec:
        return 1, None
    match = re.match(r"^(\d+):(\d+)$", spec.strip())
    if not match:
        raise ValueError(f"invalid lines spec {spec!r}; expected N:M")
    start, end = int(match.group(1)), int(match.group(2))
    if start < 1 or end < start:
        raise ValueError(f"invalid line range {spec!r}")
    return start, end


def read_file_lines(
    repo_path: str,
    file_path: str,
    *,
    start_line: int = 1,
    end_line: int | None = None,
) -> tuple[bool, str, int]:
    registry = ToolRegistry(repo_path)
    try:
        result = registry.execute(
            "read",
            path=file_path,
            start_line=start_line,
            end_line=end_line,
        )
    finally:
        registry.dispose()
    if result.is_error:
        if "not found" in result.output.lower():
            return False, f"READ_FAIL: FILE_NOT_FOUND in {file_path}", 2
        return False, f"READ_FAIL: {result.output}", 1
    return True, result.output, 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read file with optional line range")
    parser.add_argument("--repo", required=True, help="Repository root path")
    parser.add_argument("--file", required=True, help="Relative file path")
    parser.add_argument("--lines", default=None, help="Line range N:M")
    args = parser.parse_args(argv)

    try:
        start, end = _parse_lines(args.lines)
    except ValueError as e:
        print(f"READ_FAIL: INVALID_LINES {e}")
        return 1

    ok, out, code = read_file_lines(args.repo, args.file, start_line=start, end_line=end)
    print(out)
    return code if not ok else 0


if __name__ == "__main__":
    sys.exit(main())
