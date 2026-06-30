"""Tests for EDIT patch validation."""

from __future__ import annotations

from src.core.plan_validation import (
    align_old_string_in_content,
    align_old_string_to_disk,
    is_truncated_patch,
    validate_edit_patches,
)
from src.core.schemas import ExecutionPlan, ExecutionStep


def _edit_plan(old: str, new: str, *, path: str = "foo.py") -> ExecutionPlan:
    return ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="fix",
        files_to_change=[path],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                file=path,
                target="read",
                reason="ground",
                validate="ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                file=path,
                target="edit",
                reason="fix",
                validate="ok",
                old_string=old,
                new_string=new,
                intent_marker="fixed",
            ),
            ExecutionStep(
                step_id=3,
                action="VERIFY",
                file=path,
                target="verify",
                reason="check",
                validate="ok",
                intent_marker="fixed",
            ),
        ],
        risk_level="low",
    )


def test_reject_identical_old_new(tmp_path):
    target = tmp_path / "foo.py"
    target.write_text("alpha\nbeta\n", encoding="utf-8")
    plan = _edit_plan("beta", "beta")
    hard, _soft = validate_edit_patches(plan, str(tmp_path))
    assert any("identical" in e for e in hard)


def test_reject_empty_patch(tmp_path):
    target = tmp_path / "foo.py"
    target.write_text("alpha\n", encoding="utf-8")
    plan = _edit_plan("", "beta")
    hard, _soft = validate_edit_patches(plan, str(tmp_path))
    assert any("empty old_string" in e for e in hard)


def test_reject_old_string_not_found(tmp_path):
    target = tmp_path / "foo.py"
    target.write_text("alpha\n", encoding="utf-8")
    plan = _edit_plan("missing", "beta")
    hard, _soft = validate_edit_patches(plan, str(tmp_path))
    assert any("not found" in e for e in hard)


def test_reject_multiple_occurrences(tmp_path):
    target = tmp_path / "foo.py"
    target.write_text("x\nx\n", encoding="utf-8")
    plan = _edit_plan("x", "y")
    hard, _soft = validate_edit_patches(plan, str(tmp_path))
    assert any("exactly once" in e for e in hard)


def test_truncated_patch_detection():
    old = "from typing import Optional\n\nclass Student:\n    return cls(*raw) if raw else None"
    new = "from typing import Optional\n\nclass Student:\n    ret"
    assert is_truncated_patch(old, new)


def test_align_old_string_trailing_newline():
    file_content = "alpha\nbeta\n\n\n"
    old_llm = "alpha\nbeta\n\n\n\n"  # one extra trailing newline
    assert align_old_string_to_disk(old_llm, file_content) == file_content


def test_validate_passes_after_newline_alignment(tmp_path):
    target = tmp_path / "student.py"
    content = "class X:\n    pass\n\n\n"
    target.write_text(content, encoding="utf-8")
    plan = _edit_plan(content + "\n", content.replace("pass", "fixed"), path="student.py")
    hard, _soft = validate_edit_patches(plan, str(tmp_path))
    assert hard == []


def test_align_snippet_trailing_newline():
    """Reproduce SPACE-129069: LLM snippet with one extra trailing newline."""
    file_content = (
        "    @staticmethod\n"
        "    async def create(conn: Connection, name: str):\n"
        '        q = ("INSERT INTO students (name) "\n'
        '             "VALUES (\'%(name)s\')" % {\'name\': name})\n'
        "        async with conn.cursor() as cur:\n"
        "            await cur.execute(q)\n"
        "\n"
        "\n"
    )
    old_llm = file_content + "\n"  # one extra trailing newline
    assert old_llm not in file_content
    assert align_old_string_in_content(old_llm, file_content) == file_content


def test_validate_passes_after_snippet_newline_alignment(tmp_path):
    target = tmp_path / "student.py"
    on_disk = (
        "    @staticmethod\n"
        "    async def create(conn: Connection, name: str):\n"
        '        q = ("INSERT INTO students (name) "\n'
        '             "VALUES (\'%(name)s\')" % {\'name\': name})\n'
        "        async with conn.cursor() as cur:\n"
        "            await cur.execute(q)\n"
        "\n"
        "\n"
        "class Other:\n"
        "    pass\n"
    )
    target.write_text(on_disk, encoding="utf-8")
    old_llm = (
        "    @staticmethod\n"
        "    async def create(conn: Connection, name: str):\n"
        '        q = ("INSERT INTO students (name) "\n'
        '             "VALUES (\'%(name)s\')" % {\'name\': name})\n'
        "        async with conn.cursor() as cur:\n"
        "            await cur.execute(q)\n"
        "\n"
        "\n"
        "\n"
    )
    new_string = (
        "    @staticmethod\n"
        "    async def create(conn: Connection, name: str):\n"
        '        q = "INSERT INTO students (name) VALUES (%s)"\n'
        "        async with conn.cursor() as cur:\n"
        "            await cur.execute(q, (name,))\n"
        "\n"
        "\n"
        "\n"
    )
    plan = _edit_plan(old_llm, new_string, path="student.py")
    hard, _soft = validate_edit_patches(plan, str(tmp_path))
    assert hard == []
