"""Extract repo-relative file paths from free text (hypothesis evidence, summaries)."""

from __future__ import annotations

import re
from pathlib import Path

# Path-like tokens with common source/config extensions.
_PATH_TOKEN = re.compile(
    r"(?<![A-Za-z0-9_./-])"
    r"([A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)+\.(?:"
    r"py|js|ts|tsx|jsx|java|go|rb|php|cs|yaml|yml|json|toml|md|html|jinja2|j2|sql|sh"
    r"))"
    r"(?![A-Za-z0-9_./-])"
)


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
        candidate = root / raw
        if candidate.is_file():
            rel = raw.replace("\\", "/")
            seen.add(rel)
            ordered.append(rel)
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
