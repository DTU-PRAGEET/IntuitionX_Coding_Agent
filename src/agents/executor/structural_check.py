"""Deterministic structural integrity checks after Cursor edits."""

from __future__ import annotations

import ast
from pathlib import Path


_PYTHON_SUFFIXES = {".py"}
_BRACE_SUFFIXES = {
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".ts",
    ".tsx",
    ".java",
    ".c",
    ".h",
    ".cpp",
    ".hpp",
    ".cs",
    ".go",
    ".rs",
    ".kt",
    ".scala",
    ".rb",
    ".php",
}


def _check_brace_balance(content: str) -> list[str]:
    """Return errors when braces/parens/brackets are unbalanced (string-aware)."""
    pairs = {"(": ")", "[": "]", "{": "}"}
    closing = set(pairs.values())
    stack: list[str] = []
    in_string: str | None = None
    escape = False
    i = 0
    while i < len(content):
        ch = content[i]
        if escape:
            escape = False
            i += 1
            continue
        if ch == "\\" and in_string in ('"', "'", "`"):
            escape = True
            i += 1
            continue
        if in_string:
            if ch == in_string:
                in_string = None
            i += 1
            continue
        if ch in ('"', "'", "`"):
            in_string = ch
            i += 1
            continue
        if ch in pairs:
            stack.append(ch)
        elif ch in closing:
            if not stack:
                return [f"unexpected closing {ch!r} without matching opener"]
            opener = stack.pop()
            if pairs[opener] != ch:
                return [f"mismatched {opener!r} and {ch!r}"]
        i += 1
    if stack:
        return [f"unclosed {stack[-1]!r}"]
    return []


def is_python_hard_fail(file_path: str) -> bool:
    """True when structural errors should auto-fail without LLM review."""
    return Path(file_path).suffix.lower() in _PYTHON_SUFFIXES


def check_file_structure(file_path: str, content: str) -> list[str]:
    """Return structural errors for supported languages; empty when check skipped or OK."""
    suffix = Path(file_path).suffix.lower()
    if suffix in _PYTHON_SUFFIXES:
        try:
            ast.parse(content)
        except SyntaxError as exc:
            return [f"Python syntax error: {exc.msg} (line {exc.lineno})"]
        return []
    if suffix in _BRACE_SUFFIXES:
        return _check_brace_balance(content)
    return []
