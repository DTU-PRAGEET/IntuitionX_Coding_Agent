"""Path resolution utilities — sandbox paths against workspace cwd."""

from __future__ import annotations

import os
from pathlib import Path


def expand_path(file_path: str) -> str:
    """Expand ~ and normalize unicode spaces."""
    expanded = os.path.expanduser(file_path.strip())
    return expanded.replace("\u202f", " ")


def resolve_to_cwd(file_path: str, cwd: str) -> Path:
    """Resolve a path relative to cwd; absolute paths are allowed if under cwd."""
    expanded = expand_path(file_path)
    p = Path(expanded)
    if not p.is_absolute():
        p = Path(cwd) / expanded
    return p.resolve()


def assert_within_cwd(resolved: Path, cwd: str) -> None:
    """Raise ValueError if resolved path escapes cwd sandbox."""
    cwd_resolved = Path(cwd).resolve()
    resolved_str = str(resolved)
    cwd_str = str(cwd_resolved)
    if resolved_str != cwd_str and not resolved_str.startswith(cwd_str + os.sep):
        raise ValueError(f"Path traversal blocked: {file_path_display(resolved, cwd)} is outside {cwd_str}")


def resolve_path_sandbox(file_path: str, cwd: str) -> Path:
    """Resolve path and enforce cwd sandbox."""
    resolved = resolve_to_cwd(file_path, cwd)
    assert_within_cwd(resolved, cwd)
    return resolved


def file_path_display(resolved: Path, cwd: str) -> str:
    """Prefer relative display when under cwd."""
    try:
        return str(resolved.relative_to(Path(cwd).resolve()))
    except ValueError:
        return str(resolved)
