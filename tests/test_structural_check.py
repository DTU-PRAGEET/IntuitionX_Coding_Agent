"""Tests for post-edit structural integrity checks."""

from __future__ import annotations

from src.agents.executor.structural_check import check_file_structure


def test_python_syntax_ok():
    assert check_file_structure("app.py", "def foo():\n    return 1\n") == []


def test_python_syntax_error():
    errors = check_file_structure("app.py", "def foo(\n    return 1\n")
    assert len(errors) == 1
    assert "syntax error" in errors[0].lower()


def test_js_unbalanced_brace():
    errors = check_file_structure("app.js", "function foo() {\n  if (x) {\n}\n")
    assert errors
