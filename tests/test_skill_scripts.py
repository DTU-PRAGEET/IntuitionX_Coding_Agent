"""Tests for shared skill script CLI contracts."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.skill_scripts.budget import init_budget, check_and_decrement
from src.core.skill_scripts.edit_file import apply_edit, preflight_edit
from src.core.skill_scripts.file_index import build_file_index
from src.core.skill_scripts.read_file import read_file_lines
from src.core.skill_scripts.verify_edit import verify_intent_marker


def test_file_index_builds_manifest(tmp_path):
    (tmp_path / "foo.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "bar.md").write_text("# doc\n", encoding="utf-8")
    index = build_file_index(str(tmp_path))
    assert index["total_files"] >= 2
    paths = {p["path"] for p in index["paths"]}
    assert "foo.py" in paths


def test_read_file_lines(tmp_path):
    target = tmp_path / "sample.txt"
    target.write_text("line1\nline2\nline3\n", encoding="utf-8")
    ok, out, code = read_file_lines(str(tmp_path), "sample.txt", start_line=1, end_line=2)
    assert ok and code == 0
    assert "line1" in out


def test_edit_preflight_not_found():
    ok, msg, code = preflight_edit("hello world", "missing")
    assert not ok and code == 1
    assert "NOT_FOUND" in msg


def test_edit_apply_success(tmp_path):
    f = tmp_path / "edit_me.txt"
    f.write_text("old text here\n", encoding="utf-8")
    ok, out, code = apply_edit(
        str(tmp_path),
        "edit_me.txt",
        "old text here",
        "new text here",
    )
    assert ok and code == 0
    assert "new text here" in f.read_text(encoding="utf-8")


def test_verify_intent_marker(tmp_path):
    f = tmp_path / "verified.py"
    f.write_text('MARKER = "unique_token_123"\n', encoding="utf-8")
    ok, out, code = verify_intent_marker(str(tmp_path), "verified.py", "unique_token_123")
    assert ok and code == 0


def test_budget_tracker(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_BUDGET_DIR", str(tmp_path))
    init_budget("test", "thread1", 5)
    state = check_and_decrement("test", "thread1", 5)
    assert state["calls_made"] == 1
    assert state["remaining"] == 4
