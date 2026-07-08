"""Progressive validation context — diff hunks expanded to semantic parent scopes."""

from __future__ import annotations

import ast
import difflib
import re
from enum import Enum
from pathlib import Path
from typing import Any

DEFAULT_PADDING = 3
DEFAULT_SPAN_LINES = 40
_MAX_VALIDATION_SPAN = 400
_CONFIG_FILE_SUFFIXES = frozenset({".yml", ".yaml", ".json", ".toml", ".xml", ".properties"})
_HEAVY_CHANGE_RATIO = 0.5


def _read_file_lines(repo_path: str, file_path: str) -> list[str]:
    full = Path(repo_path) / file_path
    if not full.is_file():
        return []
    try:
        return full.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []


class ContextLevel(str, Enum):
    """Validation snippet expansion levels (progressive)."""

    DIFF_SEMANTIC = "diff_semantic"
    TARGET_SEMANTIC = "target_semantic"
    FULL_FILE = "full_file"


_DECL_PREFIX = (
    r"^\s*(?:(?:public|private|protected|static|final|native|synchronized|abstract|"
    r"async|export|default|@\w+(?:\([^)]*\))?)\s+)*"
)
_CLASS_RE = re.compile(r"^\s*(?:export\s+)?(?:abstract\s+)?class\s+(\w+)")


def diff_changed_line_ranges(
    original: str,
    modified: str,
) -> tuple[list[int], list[int]]:
    """Return 1-based line numbers that differ in original vs modified content."""
    orig_lines = original.splitlines()
    mod_lines = modified.splitlines()
    orig_changed: set[int] = set()
    mod_changed: set[int] = set()
    matcher = difflib.SequenceMatcher(None, orig_lines, mod_lines)
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        for i in range(i1, i2):
            orig_changed.add(i + 1)
        for j in range(j1, j2):
            mod_changed.add(j + 1)
    return sorted(orig_changed), sorted(mod_changed)


def _iter_code_chars(line: str) -> list[str]:
    """Yield non-string code characters from a single line (string/template aware)."""
    out: list[str] = []
    in_string: str | None = None
    escape = False
    i = 0
    while i < len(line):
        ch = line[i]
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
        out.append(ch)
        i += 1
    return out


def _brace_block_end(lines: list[str], start_line: int) -> int:
    """Return 1-based end line of the brace block opened at or after start_line."""
    depth = 0
    started = False
    for i in range(start_line - 1, len(lines)):
        for ch in _iter_code_chars(lines[i]):
            if ch == "{":
                depth += 1
                started = True
            elif ch == "}":
                depth -= 1
                if started and depth == 0:
                    return i + 1
    return len(lines)


def _declaration_start_lines(lines: list[str], name: str) -> list[int]:
    """Find 1-based lines that likely declare a method/function named ``name``."""
    decl_re = re.compile(
        _DECL_PREFIX + rf"(?:[\w<>\[\],\s]+\s+)?{re.escape(name)}\s*\("
    )
    hits: list[int] = []
    for i, line in enumerate(lines):
        if decl_re.search(line):
            hits.append(i + 1)
    if hits:
        return hits
    for i, line in enumerate(lines):
        if re.search(rf"\b{re.escape(name)}\s*\(", line) and "{" in line:
            hits.append(i + 1)
    return hits


def _declaration_scope(lines: list[str], name: str, near_line: int) -> tuple[int, int] | None:
    """Span of the declaration block for ``name`` closest to ``near_line``."""
    decl_lines = _declaration_start_lines(lines, name)
    if not decl_lines:
        return None
    start = min(decl_lines, key=lambda ln: abs(ln - near_line))
    end = _brace_block_end(lines, start)
    return start, end


def _class_scope(lines: list[str], name: str) -> tuple[int, int] | None:
    """Span of a class body named ``name``."""
    for i, line in enumerate(lines):
        match = _CLASS_RE.match(line)
        if match and match.group(1) == name:
            start = i + 1
            end = _brace_block_end(lines, start)
            return start, end
    return None


def _python_semantic_scope(
    lines: list[str],
    line_num: int,
    target_type: str,
    target_name: str,
) -> tuple[int, int] | None:
    """Innermost AST scope containing ``line_num``."""
    try:
        tree = ast.parse("\n".join(lines))
    except SyntaxError:
        return None

    best: tuple[int, int] | None = None
    best_size = float("inf")

    for node in ast.walk(tree):
        start = getattr(node, "lineno", None)
        end = getattr(node, "end_lineno", None) or start
        if start is None or end is None:
            continue
        if not (start <= line_num <= end):
            continue

        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if target_name and target_type in ("method", "function", "") and node.name != target_name:
                continue
            size = end - start
            if size < best_size:
                best = (start, end)
                best_size = size
        elif isinstance(node, ast.ClassDef):
            if target_name and target_type == "class" and node.name != target_name:
                continue
            size = end - start
            if size < best_size:
                best = (start, end)
                best_size = size

    return best


def _enclosing_brace_block(lines: list[str], line_num: int) -> tuple[int, int] | None:
    """Walk upward from ``line_num`` to find an enclosing ``{...}`` block."""
    for start in range(line_num, 0, -1):
        if "{" in lines[start - 1]:
            end = _brace_block_end(lines, start)
            if end >= line_num:
                return start, end
    return None


def find_semantic_scope(
    lines: list[str],
    line_num: int,
    step: dict[str, Any],
    file_path: str,
) -> tuple[int, int] | None:
    """Find the semantic parent block (method/class/function) containing ``line_num``."""
    if not lines:
        return None
    line_num = max(1, min(line_num, len(lines)))
    target_type = (step.get("target_type") or "").strip().lower()
    target_name = (step.get("target_name") or "").strip()
    suffix = Path(file_path).suffix.lower()

    if suffix == ".py":
        scope = _python_semantic_scope(lines, line_num, target_type, target_name)
        if scope:
            return scope

    if target_name and target_type == "class":
        scope = _class_scope(lines, target_name)
        if scope and scope[0] <= line_num <= scope[1]:
            return scope

    if target_name and target_type in ("method", "function", ""):
        scope = _declaration_scope(lines, target_name, line_num)
        if scope and scope[0] <= line_num <= scope[1]:
            return scope
        if scope:
            return scope

    return _enclosing_brace_block(lines, line_num)


def _union_scopes(scopes: list[tuple[int, int]], total_lines: int) -> tuple[int, int] | None:
    if not scopes:
        return None
    start = max(1, min(s for s, _ in scopes))
    end = min(total_lines, max(e for _, e in scopes))
    if end < start:
        return None
    if end - start > _MAX_VALIDATION_SPAN:
        center = (start + end) // 2
        half = _MAX_VALIDATION_SPAN // 2
        start = max(1, center - half)
        end = min(total_lines, start + _MAX_VALIDATION_SPAN)
    return start, end


def semantic_scope_for_changed_lines(
    lines: list[str],
    changed_lines: list[int],
    step: dict[str, Any],
    file_path: str,
) -> tuple[int, int] | None:
    """Expand changed lines to their nearest semantic parent scopes."""
    if not changed_lines:
        return None
    scopes: list[tuple[int, int]] = []
    for line_num in changed_lines:
        scope = find_semantic_scope(lines, line_num, step, file_path)
        if scope:
            scopes.append(scope)
    return _union_scopes(scopes, len(lines))


def _is_config_manifest_file(file_path: str) -> bool:
    """True for declarative config/manifest files without code structure."""
    return Path(file_path).suffix.lower() in _CONFIG_FILE_SUFFIXES


def _is_heavy_document_change(
    orig_changed: list[int],
    total_orig: int,
    mod_changed: list[int],
    total_mod: int,
) -> bool:
    """True when most of the document changed (typical full-manifest replacement)."""
    if total_orig <= 0 and total_mod <= 0:
        return False
    orig_ratio = len(orig_changed) / max(total_orig, 1)
    mod_ratio = len(mod_changed) / max(total_mod, 1)
    if orig_ratio >= _HEAVY_CHANGE_RATIO or mod_ratio >= _HEAVY_CHANGE_RATIO:
        return True
    if total_orig > 0 and total_mod > 0:
        size_delta = abs(total_orig - total_mod) / total_orig
        if size_delta >= 0.3 and (orig_ratio >= 0.3 or mod_ratio >= 0.3):
            return True
    return False


def _span_overlaps_file(start: int, end: int, total_lines: int) -> bool:
    """True when ``start..end`` includes at least one line of a ``total_lines`` file."""
    if total_lines <= 0:
        return False
    return start <= total_lines and end >= start


def _approximate_window(step: dict[str, Any], total_lines: int) -> tuple[int, int]:
    read_start = step.get("read_start_line")
    read_end = step.get("read_end_line")
    if read_start:
        start = int(read_start)
        end = int(read_end) if read_end else start + DEFAULT_SPAN_LINES
        return start, min(total_lines, max(start, end))

    approx = step.get("approximate_line")
    end_line = step.get("end_line")
    if approx is not None and end_line is not None:
        center = int(approx)
        return max(1, center - DEFAULT_PADDING), min(total_lines, int(end_line) + DEFAULT_PADDING)
    if approx is not None:
        center = int(approx)
        return max(1, center - DEFAULT_PADDING), min(total_lines, center + DEFAULT_SPAN_LINES)
    return 1, min(total_lines, DEFAULT_SPAN_LINES)


def pre_edit_span(
    step: dict[str, Any],
    lines: list[str],
    file_path: str,
) -> tuple[int, int]:
    """Resolve capture span before Cursor edit (target hints, not narrow anchors)."""
    approx = step.get("approximate_line")
    near = int(approx) if approx is not None else 1
    scope = find_semantic_scope(lines, near, step, file_path)
    if scope:
        return scope
    return _approximate_window(step, len(lines) or 1)


def resolve_validation_span(
    step: dict[str, Any],
    *,
    original_lines: list[str],
    modified_lines: list[str],
    file_path: str,
    level: ContextLevel = ContextLevel.DIFF_SEMANTIC,
) -> tuple[int, int]:
    """Choose validation line span for the given expansion level."""
    total_mod = len(modified_lines)
    total_orig = len(original_lines)
    if total_mod == 0 and total_orig == 0:
        return 1, 1

    if level == ContextLevel.FULL_FILE:
        return 1, max(total_mod, total_orig, 1)

    if level == ContextLevel.TARGET_SEMANTIC:
        near = int(step.get("approximate_line") or 1)
        scope = find_semantic_scope(modified_lines or original_lines, near, step, file_path)
        if scope:
            return scope
        return _approximate_window(step, max(total_mod, total_orig, 1))

    # DIFF_SEMANTIC: prefer git-diff-equivalent changed regions → semantic parent
    orig_changed, mod_changed = diff_changed_line_ranges(
        "\n".join(original_lines),
        "\n".join(modified_lines),
    )
    target_type = (step.get("target_type") or "").strip().lower()
    if _is_config_manifest_file(file_path) and target_type == "file":
        return 1, max(total_mod, total_orig, 1)
    if _is_heavy_document_change(orig_changed, total_orig, mod_changed, total_mod):
        return 1, max(total_mod, total_orig, 1)

    mod_scope = semantic_scope_for_changed_lines(modified_lines, mod_changed, step, file_path)
    orig_scope = semantic_scope_for_changed_lines(original_lines, orig_changed, step, file_path)
    scopes = [s for s in (mod_scope, orig_scope) if s]
    if scopes:
        start = min(s[0] for s in scopes)
        end = max(s[1] for s in scopes)
        end = min(end, max(total_mod, total_orig, 1))
        if not _span_overlaps_file(start, end, total_mod):
            return 1, max(total_mod, total_orig, 1)
        return start, end

    near = int(step.get("approximate_line") or 1)
    scope = find_semantic_scope(modified_lines or original_lines, near, step, file_path)
    if scope:
        if not _span_overlaps_file(scope[0], scope[1], total_mod):
            return 1, max(total_mod, total_orig, 1)
        return scope
    return _approximate_window(step, max(total_mod, total_orig, 1))


def resolve_validation_snippets(
    repo_path: str,
    step: dict[str, Any],
    pre_edit: Any,
    *,
    level: ContextLevel = ContextLevel.DIFF_SEMANTIC,
) -> tuple[str, str, int, int, ContextLevel]:
    """Build original/modified snippets for validation at the requested context level."""
    path = step.get("file") or step.get("where") or ""
    original_lines = (pre_edit.full_file_content or "").splitlines()
    if not original_lines:
        original_lines = pre_edit.original_snippet.splitlines()

    modified_lines = _read_file_lines(repo_path, path)

    start, end = resolve_validation_span(
        step,
        original_lines=original_lines,
        modified_lines=modified_lines,
        file_path=path,
        level=level,
    )

    lo = max(0, start - 1)
    hi_orig = min(len(original_lines), end)
    hi_mod = min(len(modified_lines), end)
    if modified_lines and lo >= hi_mod:
        start, end = 1, max(len(modified_lines), len(original_lines), 1)
        lo = 0
        hi_orig = len(original_lines)
        hi_mod = len(modified_lines)
        level = ContextLevel.FULL_FILE
    original_snippet = "\n".join(original_lines[lo:hi_orig])
    modified_snippet = "\n".join(modified_lines[lo:hi_mod])
    return original_snippet, modified_snippet, start, end, level


def warrants_context_expansion(
    result: Any,
    *,
    start_line: int,
    end_line: int,
    total_lines: int,
) -> bool:
    """True when a validation failure may be caused by an incomplete snippet window."""
    if result.passed or result.noop_diff or result.structural_fail:
        return False
    if total_lines > 0 and start_line <= 1 and end_line >= total_lines:
        return False
    reason_l = (result.reason or "").lower()
    snippet_signals = (
        "not show",
        "does not show",
        "outside",
        "snippet",
        "provided scope",
        "cannot confirm",
        "cannot be confirmed",
        "additional modified",
        "empty",
        "insufficient",
        "too narrow",
        "too narrow to",
        "fragment",
    )
    if any(signal in reason_l for signal in snippet_signals):
        return True
    return bool(result.missing_requirements or result.reason)
