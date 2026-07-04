"""Tests for repo file path extraction from free text."""

from __future__ import annotations

from src.core.file_paths import (
    canonical_repo_relative_path,
    extract_repo_file_paths,
    extract_repo_file_paths_from_texts,
    remap_cursor_path,
    repair_repo_from_cursor_temp_clone,
    resolve_repo_relative_path,
)


def test_extract_repo_file_paths_finds_existing_files(tmp_path):
    """Test Extract Repo File Paths Finds Existing Files for this module."""
    app = tmp_path / "sqli" / "app.py"
    app.parent.mkdir(parents=True)
    app.write_text("print('app')\n", encoding="utf-8")
    student = tmp_path / "sqli" / "dao" / "student.py"
    student.parent.mkdir(parents=True)
    student.write_text("print('student')\n", encoding="utf-8")

    text = (
        "XSS in sqli/app.py line 35; SQLi in sqli/dao/student.py via string interpolation. "
        "Ignore missing/path/here.py"
    )
    paths = extract_repo_file_paths(text, str(tmp_path))
    assert paths == ["sqli/app.py", "sqli/dao/student.py"]


def test_extract_repo_file_paths_from_texts_deduplicates(tmp_path):
    """Test Extract Repo File Paths From Texts Deduplicates for this module."""
    target = tmp_path / "sqli" / "middlewares.py"
    target.parent.mkdir(parents=True)
    target.write_text("middleware\n", encoding="utf-8")

    paths = extract_repo_file_paths_from_texts(
        [
            "see sqli/middlewares.py for cookie flags",
            "also sqli/middlewares.py httponly=False",
        ],
        str(tmp_path),
    )
    assert paths == ["sqli/middlewares.py"]


def test_resolve_repo_relative_path_strips_temp_clone_prefix(tmp_path):
    """Cursor paths like temp_clone/sqli/app.py map to sqli/app.py."""
    app = tmp_path / "sqli" / "app.py"
    app.parent.mkdir(parents=True)
    app.write_text("setup_jinja(app, autoescape=False)\n", encoding="utf-8")

    assert resolve_repo_relative_path("temp_clone/sqli/app.py", str(tmp_path)) == "sqli/app.py"
    assert resolve_repo_relative_path("sqli/app.py", str(tmp_path)) == "sqli/app.py"
    assert resolve_repo_relative_path("temp_clone/missing.py", str(tmp_path)) is None


def test_remap_cursor_path_strips_prefix_without_file(tmp_path):
    """Canonical path is returned even when the file is not on disk yet."""
    assert remap_cursor_path(".git/temp_clone/sqli/app.py", str(tmp_path)) == "sqli/app.py"
    assert canonical_repo_relative_path(".git/temp_clone/sqli/app.py") == "sqli/app.py"


def test_resolve_repo_relative_path_strips_git_temp_clone_prefix(tmp_path):
    """Cursor paths like .git/temp_clone/sqli/app.py map to sqli/app.py."""
    app = tmp_path / "sqli" / "app.py"
    app.parent.mkdir(parents=True)
    app.write_text("print('app')\n", encoding="utf-8")

    assert resolve_repo_relative_path(".git/temp_clone/sqli/app.py", str(tmp_path)) == "sqli/app.py"
    assert resolve_repo_relative_path("git/temp_clone/sqli/app.py", str(tmp_path)) == "sqli/app.py"


def test_repair_repo_from_cursor_temp_clone_materializes_files(tmp_path):
    """Files only in .git/temp_clone/ are copied to repo root."""
    internal = tmp_path / ".git" / "temp_clone" / "sqli" / "app.py"
    internal.parent.mkdir(parents=True)
    internal.write_text("setup_jinja(app, autoescape=False)\n", encoding="utf-8")

    assert repair_repo_from_cursor_temp_clone(str(tmp_path)) is True
    assert (tmp_path / "sqli" / "app.py").is_file()
    assert resolve_repo_relative_path(".git/temp_clone/sqli/app.py", str(tmp_path)) == "sqli/app.py"
