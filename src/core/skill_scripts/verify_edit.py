#!/usr/bin/env python3
"""
SCRIPT: verify_edit.py
PURPOSE: Confirm an edit applied correctly via intent marker substring check.
USAGE:
    python verify_edit.py --repo <repo_path> --file <path> --intent_marker <unique_string>

STDOUT (on success):
    VERIFY_OK: intent_marker found in <file>

STDOUT (on failure):
    VERIFY_FAIL: intent_marker not found in <file>

EXIT CODES:
    0 = success
    1 = marker not found or validation error
    2 = file not found
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.tools.edit_diff import normalize_to_lf, strip_bom


def verify_intent_marker(repo_path: str, file_path: str, intent_marker: str) -> tuple[bool, str, int]:
    resolved = Path(repo_path) / file_path
    if not resolved.exists():
        return False, f"VERIFY_FAIL: FILE_NOT_FOUND in {file_path}", 2
    raw = resolved.read_text(encoding="utf-8", errors="replace")
    _, body = strip_bom(raw)
    normalized = normalize_to_lf(body)
    marker_norm = normalize_to_lf(intent_marker)
    if marker_norm not in normalized:
        return False, f"VERIFY_FAIL: intent_marker not found in {file_path}", 1
    return True, f"VERIFY_OK: intent_marker found in {file_path}", 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify edit via intent marker")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--intent_marker", required=True)
    args = parser.parse_args(argv)

    ok, out, code = verify_intent_marker(args.repo, args.file, args.intent_marker)
    print(out)
    return code if not ok else 0


if __name__ == "__main__":
    sys.exit(main())
