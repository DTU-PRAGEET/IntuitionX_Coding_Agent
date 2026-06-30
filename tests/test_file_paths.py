"""Tests for repo file path extraction from free text."""

from __future__ import annotations

from src.core.file_paths import extract_repo_file_paths, extract_repo_file_paths_from_texts


def test_extract_repo_file_paths_finds_existing_files(tmp_path):
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
