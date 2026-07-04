#!/usr/bin/env python3
"""
SCRIPT: edit_file.py
PURPOSE: Apply a single str_replace edit to a file with pre-flight validation.
USAGE:
    python edit_file.py --repo <repo_path> --file <path> \\
        --old_string <exact_text> --new_string <replacement> [--dry_run]

STDOUT (on success):
    EDIT_OK: <file> line <N>–<M> replaced

STDOUT (on failure):
    EDIT_FAIL: OLD_STRING_NOT_FOUND in <file>
    EDIT_FAIL: OLD_STRING_AMBIGUOUS — found N occurrences in <file>
    EDIT_FAIL: INCOMPLETE — old_string still present after replacement

EXIT CODES:
    0 = success
    1 = validation error (do not retry without fixing old_string)
    2 = file not found
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.tools.edit_diff import normalize_to_lf, strip_bom
from src.tools.registry import ToolRegistry


def preflight_edit(content: str, old_string: str) -> tuple[bool, str, int]:
    """Count occurrences in normalized content. Returns (ok, message, exit_code)."""
    _, body = strip_bom(content)
    normalized = normalize_to_lf(body)
    old_norm = normalize_to_lf(old_string)
    occurrences = normalized.count(old_norm)
    if occurrences == 0:
        return False, "OLD_STRING_NOT_FOUND", 1
    if occurrences > 1:
        return False, f"OLD_STRING_AMBIGUOUS — found {occurrences} occurrences", 1
    return True, "OK", 0


def apply_edit(
    repo_path: str,
    file_path: str,
    old_string: str,
    new_string: str,
    *,
    dry_run: bool = False,
) -> tuple[bool, str, int]:
    """Apply edit."""
    root = Path(repo_path)
    resolved = root / file_path
    if not resolved.exists():
        return False, f"EDIT_FAIL: FILE_NOT_FOUND in {file_path}", 2

    raw = resolved.read_text(encoding="utf-8", errors="replace")
    ok, msg, code = preflight_edit(raw, old_string)
    if not ok:
        return False, f"EDIT_FAIL: {msg} in {file_path}", code

    if dry_run:
        return True, f"EDIT_OK: {file_path} (dry run)", 0

    registry = ToolRegistry(repo_path)
    try:
        result = registry.execute(
            "edit",
            path=file_path,
            old_string=old_string,
            new_string=new_string,
        )
    finally:
        registry.dispose()

    if result.is_error:
        out = result.output
        if "not found" in out.lower():
            return False, f"EDIT_FAIL: OLD_STRING_NOT_FOUND in {file_path}", 1
        if "multiple" in out.lower() or "ambiguous" in out.lower():
            return False, f"EDIT_FAIL: OLD_STRING_AMBIGUOUS in {file_path}", 1
        return False, f"EDIT_FAIL: {out}", 1

    updated = resolved.read_text(encoding="utf-8", errors="replace")
    _, body = strip_bom(updated)
    if normalize_to_lf(old_string) in normalize_to_lf(body):
        return False, f"EDIT_FAIL: INCOMPLETE — old_string still present in {file_path}", 1

    return True, f"EDIT_OK: {file_path} replaced", 0


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for this module."""
    parser = argparse.ArgumentParser(description="Apply single str_replace edit")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--old_string", required=True)
    parser.add_argument("--new_string", required=True)
    parser.add_argument("--dry_run", action="store_true")
    args = parser.parse_args(argv)

    ok, out, code = apply_edit(
        args.repo,
        args.file,
        args.old_string,
        args.new_string,
        dry_run=args.dry_run,
    )
    print(out)
    return code if not ok else 0


if __name__ == "__main__":
    sys.exit(main())
