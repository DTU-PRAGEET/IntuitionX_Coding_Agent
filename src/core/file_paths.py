"""Extract repo-relative file paths from free text (hypothesis evidence, summaries)."""

from __future__ import annotations

import json
import re
import shutil
import time
from pathlib import Path

from src.core.logging import get_logger

_log = get_logger("file_paths")

_DEBUG_LOG_PATH = "/Users/prageet.mishra/Documents/Intern_Project_Coding_Agent/.cursor/debug-f34ece.log"

# Cursor SDK clones into .git/temp_clone/ and reports paths with varying prefixes.
_CURSOR_PATH_PREFIXES = (
    ".git/temp_clone/",
    "git/temp_clone/",
    "temp_clone/",
)

# Path-like tokens with common source/config extensions.
_PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:"
    r"py|js|ts|tsx|jsx|java|go|rb|php|cs|yaml|yml|json|toml|md|html|jinja2|j2|sql|sh"
    r"))"
    r"(?![A-Za-z0-9_./-])"
)


def _debug_log_path_resolve(*, input_path: str, resolved: str | None, repo_path: str, hypothesis_id: str) -> None:
    """#region agent log"""
    try:
        with open(_DEBUG_LOG_PATH, "a", encoding="utf-8") as df:
            df.write(
                json.dumps(
                    {
                        "sessionId": "f34ece",
                        "hypothesisId": hypothesis_id,
                        "location": "file_paths.py:resolve_repo_relative_path",
                        "message": "path_resolve",
                        "data": {"input": input_path, "resolved": resolved, "repo": repo_path},
                        "timestamp": int(time.time() * 1000),
                    }
                )
                + "\n"
            )
    except OSError:
        pass
    """#endregion"""


def canonical_repo_relative_path(path: str) -> str:
    """Strip Cursor temp_clone path prefixes to a repo-relative path."""
    normalized = path.replace("\\", "/").strip()
    while True:
        before = normalized
        for prefix in _CURSOR_PATH_PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
        if normalized.startswith("./"):
            normalized = normalized[2:]
        if normalized == before:
            break
    return normalized.lstrip("/")


def repair_repo_from_cursor_temp_clone(repo_path: str) -> bool:
    """Copy source files from Cursor's .git/temp_clone mirror into the repo root."""
    root = Path(repo_path).resolve()
    temp_clone = root / ".git" / "temp_clone"
    if not temp_clone.is_dir():
        return False

    repaired = False
    for src in temp_clone.rglob("*"):
        if not src.is_file():
            continue
        rel = src.relative_to(temp_clone)
        if rel.parts and rel.parts[0] == ".git":
            continue
        dest = root / rel
        if dest.exists():
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        repaired = True

    if repaired:
        _log.info("repo_repaired_from_cursor_temp_clone", repo_path=str(root))
        # #region agent log
        _debug_log_path_resolve(
            input_path="repair_repo_from_cursor_temp_clone",
            resolved=str(root),
            repo_path=str(root),
            hypothesis_id="C",
        )
        # #endregion
    return repaired


def resolve_repo_relative_path(path: str, repo_path: str) -> str | None:
    """Map a Cursor- or LLM-reported path to an existing repo-relative file.

    Cursor often returns paths like ``temp_clone/sqli/app.py`` or
    ``.git/temp_clone/sqli/app.py`` while the checkout uses ``sqli/app.py``.
    """
    if not path or not repo_path:
        return None

    root = Path(repo_path).resolve()
    if not root.is_dir():
        return None

    normalized = path.replace("\\", "/").strip()
    canonical = canonical_repo_relative_path(path)
    if not canonical:
        return None

    candidates: list[str] = []
    seen: set[str] = set()

    def _add(cand: str) -> None:
        if cand and cand not in seen:
            seen.add(cand)
            candidates.append(cand)

    _add(canonical)
    _add(normalized.lstrip("./"))
    parts = canonical.split("/")
    for i in range(1, len(parts)):
        _add("/".join(parts[i:]))

    for cand in candidates:
        if (root / cand).is_file():
            if cand != normalized:
                _debug_log_path_resolve(
                    input_path=path,
                    resolved=cand,
                    repo_path=str(root),
                    hypothesis_id="A",
                )
            return cand

    internal = root / ".git" / "temp_clone" / canonical
    if internal.is_file():
        dest = root / canonical
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(internal, dest)
        _debug_log_path_resolve(
            input_path=path,
            resolved=canonical,
            repo_path=str(root),
            hypothesis_id="D",
        )
        return canonical

    _debug_log_path_resolve(
        input_path=path,
        resolved=None,
        repo_path=str(root),
        hypothesis_id="B",
    )
    return None


def remap_cursor_path(path: str, repo_path: str) -> str:
    """Resolve to an on-disk file when possible; otherwise strip Cursor path prefixes."""
    resolved = resolve_repo_relative_path(path, repo_path)
    if resolved:
        return resolved
    canonical = canonical_repo_relative_path(path)
    return canonical if canonical else path


def extract_repo_file_paths(text: str, repo_path: str) -> list[str]:
    """Return deduplicated repo-relative paths mentioned in *text* that exist on disk."""
    if not text or not repo_path:
        return []

    root = Path(repo_path).resolve()
    if not root.is_dir():
        return []

    seen: set[str] = set()
    ordered: list[str] = []
    for match in _PATH_TOKEN.finditer(text):
        raw = match.group(1).strip().lstrip("./")
        if not raw or raw in seen:
            continue
        resolved = resolve_repo_relative_path(raw, repo_path)
        if not resolved:
            continue
        if resolved in seen:
            continue
        seen.add(resolved)
        ordered.append(resolved)
    return ordered


def extract_repo_file_paths_from_texts(texts: list[str], repo_path: str) -> list[str]:
    """Extract paths from multiple text blobs, preserving first-seen order."""
    seen: set[str] = set()
    ordered: list[str] = []
    for text in texts:
        for path in extract_repo_file_paths(text, repo_path):
            if path not in seen:
                seen.add(path)
                ordered.append(path)
    return ordered
