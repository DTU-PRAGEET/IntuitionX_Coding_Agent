"""Deterministic resolution of semantic EDIT targets in source files.

Resolution order (most reliable first):
1. Python ``ast`` for ``.py`` files
2. Tree-sitter grammars for supported languages
3. Line-regex heuristics as a last resort
"""

from __future__ import annotations

import ast
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.core.schemas import ExecutionPlan, ExecutionStep, TargetType

_DEBUG_LOG_PATH = (
    Path(__file__).resolve().parents[2] / ".cursor" / "debug-f1852d.log"
)


def _debug_log(hypothesis_id: str, location: str, message: str, data: dict[str, Any]) -> None:
    # #region agent log
    try:
        payload = {
            "sessionId": "f1852d",
            "timestamp": int(time.time() * 1000),
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
        }
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload) + "\n")
    except OSError:
        pass
    # #endregion

# Optional tree-sitter — graceful degradation when not installed.
try:
    from tree_sitter_language_pack import get_parser as _get_ts_parser
except ImportError:  # pragma: no cover - exercised when dep missing
    _get_ts_parser = None  # type: ignore[assignment,misc]


@dataclass
class ResolvedTarget:
    """A uniquely resolved code location for a semantic EDIT step."""

    file: str
    start_line: int
    end_line: int
    snippet: str
    match_count: int


@dataclass(frozen=True)
class _Symbol:
    """Internal symbol candidate extracted from source structure."""

    name: str
    kind: TargetType
    start_line: int
    end_line: int
    parent: str | None


_EXT_TO_TS_LANG: dict[str, str] = {
    ".py": "python",
    ".java": "java",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    ".rb": "ruby",
    ".go": "go",
    ".rs": "rust",
    ".c": "c",
    ".h": "c",
    ".cpp": "cpp",
    ".hpp": "cpp",
    ".cs": "c_sharp",
    ".php": "php",
    ".kt": "kotlin",
    ".scala": "scala",
    ".dockerfile": "dockerfile",
    ".yml": "yaml",
    ".yaml": "yaml",
}

_ASSIGNMENT_KINDS: frozenset[TargetType] = frozenset({"variable", "assignment"})

_DOCKERFILE_NAME_PREFIX = "dockerfile"


def _read_file_lines(repo_path: str, rel_path: str) -> list[str] | None:
    full = Path(repo_path) / rel_path
    if not full.is_file():
        return None
    try:
        return full.read_text(encoding="utf-8").splitlines()
    except OSError:
        return None


def _read_file_text(repo_path: str, rel_path: str) -> str | None:
    full = Path(repo_path) / rel_path
    if not full.is_file():
        return None
    try:
        return full.read_text(encoding="utf-8")
    except OSError:
        return None


def _language_for_path(rel_path: str) -> str | None:
    path = Path(rel_path)
    suffix = path.suffix.lower()
    if suffix in _EXT_TO_TS_LANG:
        return _EXT_TO_TS_LANG[suffix]
    basename = path.name.lower()
    if basename == _DOCKERFILE_NAME_PREFIX or basename.startswith(f"{_DOCKERFILE_NAME_PREFIX}."):
        return "dockerfile"
    if basename.endswith(_DOCKERFILE_NAME_PREFIX):
        return "dockerfile"
    return None


def _immediate_parent(parents: list[str]) -> str | None:
    return parents[-1] if parents else None


def _matches_parent(symbol: _Symbol, parent_name: str | None) -> bool:
    if not parent_name:
        return True
    return symbol.parent == parent_name


def _matches_target_type(symbol: _Symbol, target_type: TargetType) -> bool:
    if target_type in _ASSIGNMENT_KINDS:
        return symbol.kind in _ASSIGNMENT_KINDS
    return symbol.kind == target_type


def _filter_symbols(
    symbols: list[_Symbol],
    *,
    target_type: TargetType,
    name: str,
    parent_name: str | None,
) -> list[_Symbol]:
    wanted = name.strip()
    hits = [
        sym
        for sym in symbols
        if sym.name == wanted
        and _matches_target_type(sym, target_type)
        and _matches_parent(sym, parent_name)
    ]
    return hits


def _rank_by_line_hint(candidates: list[_Symbol], hint: int | None) -> list[_Symbol]:
    if hint is None or len(candidates) <= 1:
        return candidates
    return sorted(candidates, key=lambda sym: abs(sym.start_line - hint))


def _find_block_end(lines: list[str], start_idx: int) -> int:
    """Heuristic block end: next dedented line at same or outer scope."""
    if start_idx >= len(lines):
        return start_idx + 1
    base_indent = len(lines[start_idx]) - len(lines[start_idx].lstrip())
    for i in range(start_idx + 1, len(lines)):
        stripped = lines[i].strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        indent = len(lines[i]) - len(lines[i].lstrip())
        if indent <= base_indent and i > start_idx:
            return i
    return len(lines)


def _snippet_from_lines(lines: list[str], start_line: int, end_line: int) -> str:
    start = max(1, start_line)
    end = min(max(end_line, start), len(lines))
    return "\n".join(lines[start - 1 : end])


def _symbols_to_resolved(
    lines: list[str],
    rel: str,
    candidates: list[_Symbol],
    *,
    line_hint: int | None,
) -> ResolvedTarget:
    ordered = _rank_by_line_hint(candidates, line_hint)
    match_count = len(ordered)
    primary = ordered[0]
    return ResolvedTarget(
        file=rel,
        start_line=primary.start_line,
        end_line=primary.end_line,
        snippet=_snippet_from_lines(lines, primary.start_line, primary.end_line),
        match_count=match_count,
    )


# ── Python AST extraction ────────────────────────────────────────────────────


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self._parents: list[str] = []
        self.symbols: list[_Symbol] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        end = getattr(node, "end_lineno", None) or node.lineno
        self.symbols.append(
            _Symbol(node.name, "class", node.lineno, end, _immediate_parent(self._parents))
        )
        self._parents.append(node.name)
        self.generic_visit(node)
        self._parents.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record_function(node.name, node.lineno, getattr(node, "end_lineno", None))
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record_function(node.name, node.lineno, getattr(node, "end_lineno", None))
        self.generic_visit(node)

    def _record_function(self, name: str, start: int, end: int | None) -> None:
        kind: TargetType = "method" if self._parents else "function"
        self.symbols.append(
            _Symbol(name, kind, start, end or start, _immediate_parent(self._parents))
        )

    def visit_Assign(self, node: ast.Assign) -> None:
        end = getattr(node, "end_lineno", None) or node.lineno
        for target in node.targets:
            for name in _assignment_names(target):
                self.symbols.append(
                    _Symbol(
                        name,
                        "assignment",
                        node.lineno,
                        end,
                        _immediate_parent(self._parents),
                    )
                )

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        end = getattr(node, "end_lineno", None) or node.lineno
        for name in _assignment_names(node.target):
            self.symbols.append(
                _Symbol(
                    name,
                    "assignment",
                    node.lineno,
                    end,
                    _immediate_parent(self._parents),
                )
            )

    def visit_Import(self, node: ast.Import) -> None:
        end = getattr(node, "end_lineno", None) or node.lineno
        for alias in node.names:
            self.symbols.append(
                _Symbol(
                    alias.asname or alias.name.split(".")[0],
                    "import",
                    node.lineno,
                    end,
                    _immediate_parent(self._parents),
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        end = getattr(node, "end_lineno", None) or node.lineno
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            self.symbols.append(
                _Symbol(
                    alias.asname or alias.name,
                    "import",
                    node.lineno,
                    end,
                    _immediate_parent(self._parents),
                )
            )
            if module:
                self.symbols.append(
                    _Symbol(
                        f"{module}.{alias.name}",
                        "import",
                        node.lineno,
                        end,
                        _immediate_parent(self._parents),
                    )
                )


def _assignment_names(node: ast.AST) -> list[str]:
    if isinstance(node, ast.Name):
        return [node.id]
    if isinstance(node, ast.Tuple):
        names: list[str] = []
        for elt in node.elts:
            names.extend(_assignment_names(elt))
        return names
    return []


def _extract_python_ast_symbols(source: str) -> list[_Symbol]:
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    visitor = _PythonSymbolVisitor()
    visitor.visit(tree)
    return visitor.symbols


# ── Tree-sitter extraction ───────────────────────────────────────────────────


def _ts_node_text(source: str, node: Any) -> str:
    return source[node.start_byte() : node.end_byte()]


def _ts_line(node: Any) -> int:
    return node.start_position().row + 1


def _ts_end_line(node: Any) -> int:
    return node.end_position().row + 1


def _ts_field_name(source: str, node: Any, field: str) -> str | None:
    child = node.child_by_field_name(field)
    if child is None:
        return None
    return _ts_node_text(source, child)


def _ts_scalar_text(source: str, node: Any) -> str | None:
    """Extract scalar text from a YAML/flow node."""
    kind = node.kind()
    if kind in (
        "plain_scalar",
        "string_scalar",
        "double_quoted_string",
        "single_quoted_string",
        "unquoted_string",
    ):
        return _ts_node_text(source, node).strip()
    for i in range(node.named_child_count()):
        text = _ts_scalar_text(source, node.named_child(i))
        if text:
            return text
    return None


def _walk_ts(node: Any, source: str, parents: list[str], emit: list[_Symbol]) -> None:
    kind = node.kind()
    next_parents = parents

    if kind in ("class_declaration", "class_definition", "class"):
        name = _ts_field_name(source, node, "name")
        if name:
            emit.append(
                _Symbol(name, "class", _ts_line(node), _ts_end_line(node), _immediate_parent(parents))
            )
            next_parents = parents + [name]

    elif kind in ("method_declaration", "method_definition", "constructor_declaration"):
        name = _ts_field_name(source, node, "name")
        if name:
            emit.append(
                _Symbol(
                    name,
                    "method",
                    _ts_line(node),
                    _ts_end_line(node),
                    _immediate_parent(parents),
                )
            )

    elif kind in ("function_declaration", "function_definition", "function_item"):
        name = _ts_field_name(source, node, "name")
        if name:
            sym_kind: TargetType = "method" if parents else "function"
            emit.append(
                _Symbol(name, sym_kind, _ts_line(node), _ts_end_line(node), _immediate_parent(parents))
            )

    elif kind == "field_declaration":
        for i in range(node.named_child_count()):
            child = node.named_child(i)
            if child.kind() != "variable_declarator":
                continue
            name = _ts_field_name(source, child, "name")
            if name:
                emit.append(
                    _Symbol(
                        name,
                        "variable",
                        _ts_line(node),
                        _ts_end_line(node),
                        _immediate_parent(parents),
                    )
                )

    elif kind in ("lexical_declaration", "variable_declaration", "short_var_declaration"):
        for i in range(node.named_child_count()):
            child = node.named_child(i)
            if child.kind() != "variable_declarator":
                continue
            name = _ts_field_name(source, child, "name")
            if name:
                emit.append(
                    _Symbol(
                        name,
                        "assignment",
                        _ts_line(node),
                        _ts_end_line(node),
                        _immediate_parent(parents),
                    )
                )

    elif kind == "arg_instruction":
        for i in range(node.named_child_count()):
            child = node.named_child(i)
            if child.kind() in ("unquoted_string", "quoted_string", "double_quoted_string"):
                emit.append(
                    _Symbol(
                        _ts_node_text(source, child),
                        "assignment",
                        _ts_line(node),
                        _ts_end_line(node),
                        None,
                    )
                )
                break

    elif kind == "env_instruction":
        for i in range(node.named_child_count()):
            pair = node.named_child(i)
            if pair.kind() != "env_pair":
                continue
            for j in range(pair.named_child_count()):
                key = pair.named_child(j)
                if key.kind() in ("unquoted_string", "quoted_string", "double_quoted_string"):
                    emit.append(
                        _Symbol(
                            _ts_node_text(source, key),
                            "assignment",
                            _ts_line(node),
                            _ts_end_line(node),
                            None,
                        )
                    )
                    break

    elif kind in ("block_mapping_pair", "flow_pair"):
        if node.named_child_count() >= 2:
            key_node = node.named_child(0)
            val_node = node.named_child(1)
            key = _ts_scalar_text(source, key_node)
            if key:
                emit.append(
                    _Symbol(
                        key,
                        "assignment",
                        _ts_line(node),
                        _ts_end_line(node),
                        _immediate_parent(parents),
                    )
                )
            nested = val_node.kind() in (
                "block_node",
                "block_mapping",
                "flow_mapping",
                "block_sequence",
            )
            child_parents = parents + [key] if key and nested else parents
            _walk_ts(val_node, source, child_parents, emit)
        return

    elif kind in ("import_statement", "import_declaration", "import_from_statement"):
        for i in range(node.named_child_count()):
            child = node.named_child(i)
            child_kind = child.kind()
            if child_kind in ("import_clause", "import_specifier", "identifier", "scoped_identifier"):
                text = _ts_node_text(source, child).strip()
                if text and text not in {"from", "import", "as"}:
                    emit.append(
                        _Symbol(
                            text,
                            "import",
                            _ts_line(node),
                            _ts_end_line(node),
                            _immediate_parent(parents),
                        )
                    )

    for i in range(node.named_child_count()):
        _walk_ts(node.named_child(i), source, next_parents, emit)


def _extract_tree_sitter_symbols(source: str, language: str) -> list[_Symbol]:
    if _get_ts_parser is None:
        return []
    try:
        parser = _get_ts_parser(language)
        tree = parser.parse(source)
    except Exception:
        return []
    emit: list[_Symbol] = []
    _walk_ts(tree.root_node(), source, [], emit)
    return emit


def _import_name_matches(symbol: _Symbol, wanted: str) -> bool:
    return symbol.kind == "import" and symbol.name == wanted


def _filter_symbols_with_imports(
    symbols: list[_Symbol],
    *,
    target_type: TargetType,
    name: str,
    parent_name: str | None,
) -> list[_Symbol]:
    if target_type != "import":
        return _filter_symbols(symbols, target_type=target_type, name=name, parent_name=parent_name)
    wanted = name.strip()
    hits = [
        sym
        for sym in symbols
        if sym.kind == "import"
        and _import_name_matches(sym, wanted)
        and _matches_parent(sym, parent_name)
    ]
    return hits


def _resolve_with_structure(
    source: str,
    rel_path: str,
    *,
    target_type: TargetType,
    name: str,
    parent_name: str | None,
    line_hint: int | None,
) -> list[_Symbol]:
    symbols: list[_Symbol] = []
    language = _language_for_path(rel_path)

    if language == "python":
        symbols.extend(_extract_python_ast_symbols(source))

    if language and _get_ts_parser is not None:
        ts_symbols = _extract_tree_sitter_symbols(source, language)
        if not symbols:
            symbols = ts_symbols
        elif language != "python":
            symbols = ts_symbols

    if not symbols:
        return []

    return _filter_symbols_with_imports(
        symbols,
        target_type=target_type,
        name=name,
        parent_name=parent_name,
    )


# ── Regex fallback (last resort) ─────────────────────────────────────────────


def _pattern_for_target(target_type: TargetType, name: str) -> re.Pattern[str] | None:
    escaped = re.escape(name)
    patterns: dict[TargetType, str] = {
        "function": rf"^\s*(?:async\s+)?(?:def|function)\s+{escaped}\s*[\(<]",
        "method": rf"^\s*(?:async\s+)?(?:def|function|public|private|protected)\s+[\w<>\[\],\s]*\s+{escaped}\s*\(",
        "class": rf"^\s*(?:class|interface|enum)\s+{escaped}\b",
        "variable": rf"^\s*(?:private|protected|public)?\s*[\w<>\[\],\s]+\s+{escaped}\s*[=;]|^\s*(?:ARG|ENV)\s+{escaped}\s*=",
        "assignment": (
            rf"^\s*(?:const|let|var)?\s*{escaped}\s*="
            rf"|^\s*(?:ARG|ENV)\s+{escaped}\s*="
            rf"|^\s*{escaped}\s*[:=]"
        ),
        "import": rf"^\s*(?:import|from|#include)\s+.*\b{escaped}\b",
    }
    pat = patterns.get(target_type)
    return re.compile(pat) if pat else None


def _parent_scope_start(lines: list[str], line_idx: int) -> str | None:
    pattern = re.compile(
        r"^\s*(?:class|def|function|interface|enum|public|private|protected)\s+(\w+)"
    )
    for i in range(line_idx, -1, -1):
        match = pattern.match(lines[i])
        if match:
            return match.group(1)
    return None


def _resolve_with_regex(
    lines: list[str],
    *,
    target_type: TargetType,
    name: str,
    parent_name: str | None,
    line_hint: int | None,
) -> list[_Symbol]:
    pattern = _pattern_for_target(target_type, name)
    if pattern is None:
        return []

    hits: list[_Symbol] = []
    for i, line in enumerate(lines):
        if not pattern.search(line):
            continue
        if parent_name and _parent_scope_start(lines, i) != parent_name:
            continue
        start = i + 1
        end = _find_block_end(lines, i)
        hits.append(_Symbol(name, target_type, start, end, _parent_scope_start(lines, i)))
    return _rank_by_line_hint(hits, line_hint)


# ── Public resolution API ────────────────────────────────────────────────────


def resolve_semantic_target(
    repo_path: str,
    step: ExecutionStep | dict[str, Any],
) -> ResolvedTarget | None:
    """Locate a semantic target in the repository; None when file missing."""
    if isinstance(step, ExecutionStep):
        data = step.model_dump()
    else:
        data = dict(step)

    rel = data.get("file") or ""
    target_type = data.get("target_type")
    if not rel or not target_type:
        return None

    lines = _read_file_lines(repo_path, rel)
    if lines is None:
        return None

    approx = data.get("approximate_line")
    parent = data.get("parent_name")
    end_line = data.get("end_line")
    name = (data.get("target_name") or "").strip()

    if target_type == "file":
        snippet = "\n".join(lines[: min(40, len(lines))])
        return ResolvedTarget(
            file=rel,
            start_line=1,
            end_line=len(lines),
            snippet=snippet,
            match_count=1,
        )

    if target_type == "block":
        if not approx:
            return None
        start = max(1, int(approx))
        end = int(end_line) if end_line else _find_block_end(lines, start - 1)
        end = min(end, len(lines))
        snippet = "\n".join(lines[start - 1 : end])
        return ResolvedTarget(
            file=rel,
            start_line=start,
            end_line=end,
            snippet=snippet,
            match_count=1,
        )

    if not name:
        return None

    source = "\n".join(lines)
    language = _language_for_path(rel)
    struct_candidates = _resolve_with_structure(
        source,
        rel,
        target_type=target_type,
        name=name,
        parent_name=parent,
        line_hint=approx,
    )
    regex_candidates = _resolve_with_regex(
        lines,
        target_type=target_type,
        name=name,
        parent_name=parent,
        line_hint=approx,
    )
    candidates = struct_candidates or regex_candidates
    # #region agent log
    _debug_log(
        "A",
        "semantic_target.py:resolve_semantic_target",
        "resolution_attempt",
        {
            "file": rel,
            "language": language,
            "target_type": target_type,
            "target_name": name,
            "parent_name": parent,
            "struct_count": len(struct_candidates),
            "regex_count": len(regex_candidates),
            "resolved_count": len(candidates),
            "approximate_line": approx,
        },
    )
    # #endregion

    if not candidates:
        return None

    return _symbols_to_resolved(lines, rel, candidates, line_hint=approx)


def is_semantic_edit(step: ExecutionStep | dict[str, Any]) -> bool:
    """True when step uses semantic fields instead of legacy patches."""
    if isinstance(step, ExecutionStep):
        return bool(step.instruction and step.target_type)
    return bool(step.get("instruction") and step.get("target_type"))


def validate_semantic_target_unique(
    repo_path: str,
    step: ExecutionStep,
    *,
    create_files: bool = False,
) -> list[str]:
    """Return hard validation errors for one semantic EDIT step."""
    errors: list[str] = []
    sid = step.step_id
    path = step.file

    if not step.instruction:
        errors.append(f"step {sid}: EDIT missing instruction")
    if not step.target_type:
        errors.append(f"step {sid}: EDIT missing target_type")
    if not step.intent_marker:
        errors.append(f"step {sid}: EDIT missing intent_marker")

    full = Path(repo_path) / path
    if not full.is_file():
        if not create_files and step.target_type != "file":
            errors.append(f"step {sid}: target file {path!r} not found")
            return errors

    if step.target_type == "block" and not step.end_line:
        errors.append(f"step {sid}: block target requires end_line")

    if step.target_type not in ("file", "block") and not (step.target_name or "").strip():
        if not (create_files and step.target_type == "file"):
            errors.append(f"step {sid}: EDIT missing target_name for {step.target_type}")

    resolved = resolve_semantic_target(repo_path, step)
    if resolved is None and full.is_file():
        errors.append(
            f"step {sid}: could not resolve {step.target_type} "
            f"{step.target_name!r} in {path}"
        )
    elif resolved is not None and resolved.match_count != 1:
        parent_note = f" in parent {step.parent_name!r}" if step.parent_name else ""
        errors.append(
            f"step {sid}: target {step.target_name!r} ({step.target_type}){parent_note} "
            f"must be unique in {path}, found {resolved.match_count} matches"
        )
    elif resolved is not None and step.parent_name and step.target_type not in ("file", "block", "import"):
        source = _read_file_text(repo_path, path) or ""
        symbols = _resolve_with_structure(
            source,
            path,
            target_type=step.target_type,
            name=(step.target_name or "").strip(),
            parent_name=None,
            line_hint=step.approximate_line,
        )
        typed = [s for s in symbols if _matches_target_type(s, step.target_type)]
        if typed and not any(s.parent == step.parent_name for s in typed):
            errors.append(
                f"step {sid}: target {step.target_name!r} ({step.target_type}) "
                f"not found in parent scope {step.parent_name!r} in {path}"
            )

    return errors


def validate_semantic_edits(
    plan: ExecutionPlan,
    repo_path: str,
) -> tuple[list[str], list[str]]:
    """Return (hard_errors, soft_flags) for semantic EDIT steps."""
    hard: list[str] = []
    soft: list[str] = []

    for step in plan.execution_steps:
        if step.action != "EDIT":
            continue
        if not is_semantic_edit(step):
            if step.old_string or step.new_string:
                soft.append(
                    f"step {step.step_id}: legacy patch fields present; "
                    "prefer semantic instruction"
                )
            else:
                hard.append(f"step {step.step_id}: EDIT has neither semantic nor patch fields")
            continue
        hard.extend(
            validate_semantic_target_unique(
                repo_path, step, create_files=plan.create_files
            )
        )

    return hard, soft
