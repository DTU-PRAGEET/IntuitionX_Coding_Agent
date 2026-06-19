"""Isolation tests for L1 primitive tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.registry import ToolRegistry


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture
def registry(workspace: Path) -> ToolRegistry:
    return ToolRegistry(str(workspace))


def test_path_traversal_blocked(registry: ToolRegistry) -> None:
    result = registry.execute("read", path="../../etc/passwd")
    assert result.is_error
    assert "Path traversal blocked" in result.output


def test_write_read_roundtrip(registry: ToolRegistry, workspace: Path) -> None:
    w = registry.execute("write", path="a.txt", content="hello\nworld")
    assert not w.is_error
    r = registry.execute("read", path="a.txt", start_line=1)
    assert not r.is_error
    assert "hello" in r.output
    assert (workspace / "a.txt").exists()


def test_edit_unique_match(registry: ToolRegistry) -> None:
    registry.execute("write", path="b.txt", content="foo foo foo")
    bad = registry.execute("edit", path="b.txt", old_string="foo", new_string="bar")
    assert bad.is_error
    assert "appears 3 times" in bad.output
    registry.execute("write", path="b.txt", content="unique text")
    ok = registry.execute("edit", path="b.txt", old_string="unique text", new_string="patched")
    assert not ok.is_error


def test_read_line_bounds(registry: ToolRegistry) -> None:
    lines = "\n".join(f"line{i}" for i in range(1, 601))
    registry.execute("write", path="big.txt", content=lines)
    result = registry.execute("read", path="big.txt", start_line=1)
    assert not result.is_error
    numbered = [ln for ln in result.output.splitlines() if ln and not ln.startswith("[")]
    assert len(numbered) <= 500


def test_bash_echo(registry: ToolRegistry) -> None:
    result = registry.execute("bash", command="echo ok")
    assert not result.is_error
    assert "ok" in result.output


def test_bash_timeout(registry: ToolRegistry) -> None:
    result = registry.execute("bash", command="sleep 2", timeout=1)
    assert result.is_error
    assert "timed out" in result.output.lower()


def test_find_glob(registry: ToolRegistry) -> None:
    registry.execute("write", path="src/x.py", content="x")
    result = registry.execute("find", pattern="**/*.py", path=".")
    assert not result.is_error
    payload = json.loads(result.output.split("[Result Truncated")[0].strip())
    assert any("x.py" in f for f in payload["files"])


def test_grep_pattern(registry: ToolRegistry) -> None:
    registry.execute("write", path="grepme.txt", content="needle here")
    result = registry.execute("grep", pattern="needle", path=".")
    assert not result.is_error
    assert "needle" in result.output


def test_ls_lists_entries(registry: ToolRegistry) -> None:
    registry.execute("write", path="one.txt", content="1")
    registry.execute("write", path="two.txt", content="2")
    result = registry.execute("ls", path=".")
    assert not result.is_error
    assert "one.txt" in result.output
    assert "two.txt" in result.output


def test_unknown_tool(registry: ToolRegistry) -> None:
    result = registry.execute("nonexistent", path=".")
    assert result.is_error
    assert "Unknown tool" in result.output


def test_registry_lists_seven_primitives(registry: ToolRegistry) -> None:
    names = registry.list_names()
    assert names == ["read", "write", "edit", "bash", "find", "grep", "ls"]
