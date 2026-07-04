"""Isolation tests for L1 primitive tools."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.tools.registry import ToolRegistry


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Workspace for this module."""
    return tmp_path


@pytest.fixture
def registry(workspace: Path) -> ToolRegistry:
    """Registry for this module."""
    return ToolRegistry(str(workspace))


def test_path_traversal_blocked(registry: ToolRegistry) -> None:
    """Test Path Traversal Blocked for this module."""
    result = registry.execute("read", path="../../etc/passwd")
    assert result.is_error
    assert "Path traversal blocked" in result.output


def test_write_read_roundtrip(registry: ToolRegistry, workspace: Path) -> None:
    """Test Write Read Roundtrip for this module."""
    w = registry.execute("write", path="a.txt", content="hello\nworld")
    assert not w.is_error
    r = registry.execute("read", path="a.txt", start_line=1)
    assert not r.is_error
    assert "hello" in r.output
    assert (workspace / "a.txt").exists()


def test_edit_unique_match(registry: ToolRegistry) -> None:
    """Test Edit Unique Match for this module."""
    registry.execute("write", path="b.txt", content="foo foo foo")
    bad = registry.execute("edit", path="b.txt", old_string="foo", new_string="bar")
    assert bad.is_error
    assert "occurrences" in bad.output
    registry.execute("write", path="b.txt", content="unique text")
    ok = registry.execute("edit", path="b.txt", old_string="unique text", new_string="patched")
    assert not ok.is_error
    assert "Successfully replaced 1 block(s)" in ok.output


def test_edit_crlf_preserves_line_endings(registry: ToolRegistry, workspace: Path) -> None:
    """Test Edit Crlf Preserves Line Endings for this module."""
    registry.execute("write", path="crlf.txt", content="alpha\r\nbeta\r\n")
    ok = registry.execute("edit", path="crlf.txt", old_string="beta", new_string="BETA")
    assert not ok.is_error
    assert (workspace / "crlf.txt").read_bytes() == b"alpha\r\nBETA\r\n"


def test_edit_multi_hunk(registry: ToolRegistry, workspace: Path) -> None:
    """Test Edit Multi Hunk for this module."""
    registry.execute("write", path="multi.txt", content="one\ntwo\nthree\n")
    ok = registry.execute(
        "edit",
        path="multi.txt",
        edits=[
            {"old_string": "one", "new_string": "ONE"},
            {"old_string": "three", "new_string": "THREE"},
        ],
    )
    assert not ok.is_error
    assert (workspace / "multi.txt").read_text(encoding="utf-8") == "ONE\ntwo\nTHREE\n"


def test_edit_json_string_edits(registry: ToolRegistry, workspace: Path) -> None:
    """Test Edit Json String Edits for this module."""
    registry.execute("write", path="json.txt", content="before\n")
    ok = registry.execute(
        "edit",
        path="json.txt",
        edits='[{"old_string": "before", "new_string": "after"}]',
    )
    assert not ok.is_error
    assert (workspace / "json.txt").read_text(encoding="utf-8") == "after\n"


def test_edit_old_text_alias(registry: ToolRegistry, workspace: Path) -> None:
    """Test Edit Old Text Alias for this module."""
    registry.execute("write", path="alias.txt", content="keep\n")
    ok = registry.execute(
        "edit",
        path="alias.txt",
        edits=[{"oldText": "keep", "newText": "saved"}],
    )
    assert not ok.is_error
    assert (workspace / "alias.txt").read_text(encoding="utf-8") == "saved\n"


def test_edit_fixture_regression(registry: ToolRegistry, workspace: Path) -> None:
    """Test Edit Fixture Regression for this module."""
    fixture_rel = "src/example/handler.py"
    target = workspace / fixture_rel
    target.parent.mkdir(parents=True, exist_ok=True)
    content = (
        'def search_users(query: str) -> list[dict[str, Any]]:\n'
        '    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
        '    return [{"sql": sql}]\n'
    )
    target.write_text(content, encoding="utf-8")

    old_line = '    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"'
    new_line = '    sql = "SELECT * FROM users WHERE name LIKE %s"'
    ok = registry.execute(
        "edit",
        path=fixture_rel,
        old_string=old_line,
        new_string=new_line,
    )
    assert not ok.is_error, ok.output
    patched = target.read_text(encoding="utf-8")
    assert new_line in patched
    assert 'f"SELECT * FROM users' not in patched


def test_edit_not_found_returns_actionable_error(registry: ToolRegistry) -> None:
    """Test Edit Not Found Returns Actionable Error for this module."""
    registry.execute("write", path="miss.txt", content="actual content\n")
    bad = registry.execute("edit", path="miss.txt", old_string="wrong guess", new_string="x")
    assert bad.is_error
    assert "Copy old_string verbatim" in bad.output


def test_read_line_bounds(registry: ToolRegistry) -> None:
    """Test Read Line Bounds for this module."""
    lines = "\n".join(f"line{i}" for i in range(1, 601))
    registry.execute("write", path="big.txt", content=lines)
    result = registry.execute("read", path="big.txt", start_line=1)
    assert not result.is_error
    numbered = [ln for ln in result.output.splitlines() if ln and not ln.startswith("[")]
    assert len(numbered) <= 500


def test_bash_echo(registry: ToolRegistry) -> None:
    """Test Bash Echo for this module."""
    result = registry.execute("bash", command="echo ok")
    assert not result.is_error
    assert "ok" in result.output


def test_bash_timeout(registry: ToolRegistry) -> None:
    """Test Bash Timeout for this module."""
    result = registry.execute("bash", command="sleep 2", timeout=1)
    assert result.is_error
    assert "timed out" in result.output.lower()


def test_find_glob(registry: ToolRegistry) -> None:
    """Test Find Glob for this module."""
    registry.execute("write", path="src/x.py", content="x")
    result = registry.execute("find", pattern="**/*.py", path=".")
    assert not result.is_error
    payload = json.loads(result.output.split("[Result Truncated")[0].strip())
    assert any("x.py" in f for f in payload["files"])


def test_grep_pattern(registry: ToolRegistry) -> None:
    """Test Grep Pattern for this module."""
    registry.execute("write", path="grepme.txt", content="needle here")
    result = registry.execute("grep", pattern="needle", path=".")
    assert not result.is_error
    assert "needle" in result.output


def test_ls_lists_entries(registry: ToolRegistry) -> None:
    """Test Ls Lists Entries for this module."""
    registry.execute("write", path="one.txt", content="1")
    registry.execute("write", path="two.txt", content="2")
    result = registry.execute("ls", path=".")
    assert not result.is_error
    assert "one.txt" in result.output
    assert "two.txt" in result.output


def test_unknown_tool(registry: ToolRegistry) -> None:
    """Test Unknown Tool for this module."""
    result = registry.execute("nonexistent", path=".")
    assert result.is_error
    assert "Unknown tool" in result.output


def test_registry_lists_seven_primitives(registry: ToolRegistry) -> None:
    """Test Registry Lists Seven Primitives for this module."""
    names = registry.list_names()
    assert names == ["read", "write", "edit", "bash", "find", "grep", "ls"]
