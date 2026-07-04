"""Unit tests for edit matching engine."""

from __future__ import annotations

import pytest

from src.tools.edit_diff import (
    EditError,
    EditReplacement,
    apply_edits_to_normalized_content,
    detect_line_ending,
    normalize_for_fuzzy_match,
    normalize_to_lf,
    restore_line_endings,
    strip_bom,
)
from src.tools.edit_args import prepare_edit_arguments


def test_basic_exact_replace() -> None:
    """Test Basic Exact Replace for this module."""
    content = "alpha\nbeta\ngamma\n"
    _, new = apply_edits_to_normalized_content(
        content,
        [EditReplacement(old_text="beta", new_text="BETA")],
        "sample.txt",
    )
    assert new == "alpha\nBETA\ngamma\n"


def test_not_found_includes_hint() -> None:
    """Test Not Found Includes Hint for this module."""
    with pytest.raises(EditError, match="Could not find the exact text"):
        apply_edits_to_normalized_content(
            "hello world\n",
            [EditReplacement(old_text="missing block", new_text="x")],
            "sample.txt",
        )


def test_not_found_includes_nearby_lines() -> None:
    """Test Not Found Includes Nearby Lines for this module."""
    content = 'sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
    with pytest.raises(EditError, match="Nearby lines in file"):
        apply_edits_to_normalized_content(
            content,
            [EditReplacement(old_text="cursor.execute(sql)", new_text="patched")],
            "UserSearch.py",
        )


def test_duplicate_match_rejected() -> None:
    """Test Duplicate Match Rejected for this module."""
    with pytest.raises(EditError, match="Found 3 occurrences"):
        apply_edits_to_normalized_content(
            "foo foo foo\n",
            [EditReplacement(old_text="foo", new_text="bar")],
            "sample.txt",
        )


def test_crlf_file_lf_old_text() -> None:
    """Test Crlf File Lf Old Text for this module."""
    content = "line one\r\nline two\r\n"
    ending = detect_line_ending(content)
    assert ending == "\r\n"
    normalized = normalize_to_lf(content)
    _, new = apply_edits_to_normalized_content(
        normalized,
        [EditReplacement(old_text="line two", new_text="LINE TWO")],
        "sample.txt",
    )
    restored = restore_line_endings(new, ending)
    assert restored == "line one\r\nLINE TWO\r\n"


def test_trailing_whitespace_fuzzy_match() -> None:
    """Test Trailing Whitespace Fuzzy Match for this module."""
    content = "def run():   \n    return 1\n"
    old = "def run():\n    return 1"
    _, new = apply_edits_to_normalized_content(
        normalize_to_lf(content),
        [EditReplacement(old_text=old, new_text="def run():\n    return 2")],
        "sample.py",
    )
    assert "return 2" in new


def test_bom_preserved_on_write_path() -> None:
    """Test Bom Preserved On Write Path for this module."""
    bom, stripped = strip_bom("\ufeffhello")
    assert bom == "\ufeff"
    assert stripped == "hello"
    _, new = apply_edits_to_normalized_content(
        stripped,
        [EditReplacement(old_text="hello", new_text="world")],
        "sample.txt",
    )
    assert new == "world"


def test_overlapping_edits_rejected() -> None:
    """Test Overlapping Edits Rejected for this module."""
    content = "abcdef\n"
    with pytest.raises(EditError, match="overlap"):
        apply_edits_to_normalized_content(
            content,
            [
                EditReplacement(old_text="abc", new_text="xxx"),
                EditReplacement(old_text="cde", new_text="yyy"),
            ],
            "sample.txt",
        )


def test_no_op_edit_rejected() -> None:
    """Test No Op Edit Rejected for this module."""
    with pytest.raises(EditError, match="identical content"):
        apply_edits_to_normalized_content(
            "same\n",
            [EditReplacement(old_text="same", new_text="same")],
            "sample.txt",
        )


def test_empty_old_text_rejected() -> None:
    """Test Empty Old Text Rejected for this module."""
    with pytest.raises(EditError, match="must not be empty"):
        apply_edits_to_normalized_content(
            "content\n",
            [EditReplacement(old_text="", new_text="x")],
            "sample.txt",
        )


def test_multi_edit_disjoint() -> None:
    """Test Multi Edit Disjoint for this module."""
    content = "aaa\nbbb\nccc\n"
    _, new = apply_edits_to_normalized_content(
        content,
        [
            EditReplacement(old_text="aaa", new_text="AAA"),
            EditReplacement(old_text="ccc", new_text="CCC"),
        ],
        "sample.txt",
    )
    assert new == "AAA\nbbb\nCCC\n"


def test_prepare_legacy_arguments() -> None:
    """Test Prepare Legacy Arguments for this module."""
    path, edits = prepare_edit_arguments(
        path="a.py",
        old_string="old",
        new_string="new",
    )
    assert path == "a.py"
    assert len(edits) == 1
    assert edits[0].old_text == "old"
    assert edits[0].new_text == "new"


def test_prepare_json_string_edits() -> None:
    """Test Prepare Json String Edits for this module."""
    path, edits = prepare_edit_arguments(
        path="a.py",
        edits='[{"old_string": "x", "new_string": "y"}]',
    )
    assert path == "a.py"
    assert edits[0].old_text == "x"


def test_prepare_old_text_alias() -> None:
    """Test Prepare Old Text Alias for this module."""
    path, edits = prepare_edit_arguments(
        path="a.py",
        edits=[{"oldText": "a", "newText": "b"}],
    )
    assert edits[0].old_text == "a"
    assert edits[0].new_text == "b"


def test_prepare_rejects_list_as_old_string() -> None:
    """Test Prepare Rejects List As Old String for this module."""
    with pytest.raises(EditError, match="must be a string, got list"):
        prepare_edit_arguments(path="a.py", old_string=["bad"], new_string="x")


def test_normalize_for_fuzzy_match_smart_quotes() -> None:
    """Test Normalize For Fuzzy Match Smart Quotes for this module."""
    assert normalize_for_fuzzy_match("\u201chello\u201d") == '"hello"'
