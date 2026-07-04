"""Tests for path resolution — pseudo-absolute paths and sandbox enforcement."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.tools.path_utils import resolve_path_sandbox, resolve_to_cwd
from src.tools.registry import ToolRegistry


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repo for this module."""
    target = tmp_path / "sqli" / "routes.py"
    target.parent.mkdir(parents=True)
    target.write_text("from flask import Blueprint\n", encoding="utf-8")
    return tmp_path


def test_read_relative_path(repo: Path) -> None:
    """Test Read Relative Path for this module."""
    registry = ToolRegistry(str(repo))
    try:
        result = registry.execute("read", path="sqli/routes.py", start_line=1)
        assert not result.is_error
        assert "Blueprint" in result.output
    finally:
        registry.dispose()


def test_read_pseudo_absolute_path(repo: Path) -> None:
    """Test Read Pseudo Absolute Path for this module."""
    registry = ToolRegistry(str(repo))
    try:
        result = registry.execute("read", path="/sqli/routes.py", start_line=1)
        assert not result.is_error
        assert "Blueprint" in result.output
    finally:
        registry.dispose()


def test_read_full_absolute_path(repo: Path) -> None:
    """Test Read Full Absolute Path for this module."""
    full = str((repo / "sqli" / "routes.py").resolve())
    registry = ToolRegistry(str(repo))
    try:
        result = registry.execute("read", path=full, start_line=1)
        assert not result.is_error
        assert "Blueprint" in result.output
    finally:
        registry.dispose()


def test_resolve_to_cwd_pseudo_absolute(repo: Path) -> None:
    """Test Resolve To Cwd Pseudo Absolute for this module."""
    resolved = resolve_to_cwd("/sqli/routes.py", str(repo))
    assert resolved == (repo / "sqli" / "routes.py").resolve()
    assert resolved.exists()


def test_path_traversal_blocked(repo: Path) -> None:
    """Test Path Traversal Blocked for this module."""
    registry = ToolRegistry(str(repo))
    try:
        result = registry.execute("read", path="../../etc/passwd")
        assert result.is_error
        assert "Path traversal blocked" in result.output
    finally:
        registry.dispose()


def test_outside_absolute_path_blocked(repo: Path) -> None:
    """Test Outside Absolute Path Blocked for this module."""
    registry = ToolRegistry(str(repo))
    try:
        result = registry.execute("read", path="/etc/passwd")
        assert result.is_error
        assert "Path traversal blocked" in result.output or "file not found" in result.output.lower()
    finally:
        registry.dispose()


def test_resolve_path_sandbox_blocks_outside_absolute(repo: Path) -> None:
    """Test Resolve Path Sandbox Blocks Outside Absolute for this module."""
    with pytest.raises(ValueError, match="Path traversal blocked"):
        resolve_path_sandbox("/etc/passwd", str(repo))
