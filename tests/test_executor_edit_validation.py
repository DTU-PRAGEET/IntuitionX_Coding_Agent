"""Tests for executor pre-edit validation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from src.agents.executor.agent import _execute_edit, run_executor
from src.agents.executor.tools.builder import build_executor_tools
from src.core.repo_snapshot import sha256_text
from src.core.schemas import ExecutionPlan, ExecutionStep
from src.integrations.cursor.gateway import LocalPatchCursorGateway
from src.integrations.cursor.schemas import CursorEditResult


def test_executor_rejects_identical_patch_without_edit_tool(tmp_path):
    (tmp_path / "f.py").write_text("unchanged\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="bad",
        files_to_change=["f.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                where="f.py",
                what="noop",
                why="bad plan",
                validate="n/a",
                old_string="unchanged",
                new_string="unchanged",
            ),
        ],
        risk_level="low",
    )
    registry, _ = build_executor_tools(str(tmp_path))
    try:
        ok, out, _devs = _execute_edit(
            registry,
            plan.execution_steps[0].model_dump(),
            plan,
            create_files=False,
            llm=MagicMock(),
            gateway=LocalPatchCursorGateway(tmp_path),
        )
    finally:
        registry.dispose()
    assert ok is False
    assert "identical" in out.lower()


def test_executor_snapshot_mismatch_raises(tmp_path):
    (tmp_path / "f.py").write_text("x\n", encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="T-1",
        root_hypothesis="snap",
        files_to_change=["f.py"],
        planner_snapshot_head="deadbeef" * 5,
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where="f.py",
                what="read",
                why="r",
                validate="ok",
            ),
        ],
        risk_level="low",
    )
    with pytest.raises(RuntimeError, match="snapshot mismatch"):
        run_executor(plan, str(tmp_path), gateway=LocalPatchCursorGateway(tmp_path))


def test_executor_accepts_successful_cursor_edit_without_llm_fallback(tmp_path):
    """Regression: Cursor applies fix but line-level completeness falsely fails."""
    old_block = (
        "    setup_jinja(app, loader=PackageLoader('sqli', 'templates'),\n"
        "                context_processors=[csrf_processor, auth_user_processor],\n"
        "                autoescape=False)\n"
    )
    new_block = (
        "    setup_jinja(\n"
        "        app,\n"
        "        loader=PackageLoader('sqli', 'templates'),\n"
        "        context_processors=[csrf_processor, auth_user_processor],\n"
        "        autoescape=True,\n"
        "    )\n"
    )
    old_file = (
        "from pkg import app\n"
        "from pkg.loader import PackageLoader\n\n"
        f"{old_block}"
        "    setup_database(app)\n"
    )
    new_file = (
        "from pkg import app\n"
        "from pkg.loader import PackageLoader\n\n"
        f"{new_block}"
        "    setup_database(app)\n"
    )
    target = tmp_path / "sqli" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text(old_file, encoding="utf-8")

    plan = ExecutionPlan(
        ticket_id="SPACE-129069",
        root_hypothesis="Enable Jinja autoescape",
        files_to_change=["sqli/app.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="READ",
                where="sqli/app.py",
                what="read app",
                why="context",
                validate="ok",
            ),
            ExecutionStep(
                step_id=2,
                action="EDIT",
                where="sqli/app.py",
                what="Enable autoescape",
                why="XSS fix",
                validate="autoescape=True",
                old_string=old_file,
                new_string=new_file,
            ),
        ],
        risk_level="medium",
    )
    mock_llm = MagicMock()
    result = run_executor(
        plan,
        str(tmp_path),
        llm=mock_llm,
        gateway=LocalPatchCursorGateway(tmp_path),
    )
    assert result.steps_completed == 2
    assert "autoescape=True" in target.read_text(encoding="utf-8")
    assert "autoescape=False" not in target.read_text(encoding="utf-8")
    mock_llm.invoke.assert_not_called()
    assert sha256_text(target.read_text(encoding="utf-8")) == sha256_text(new_file)


def test_executor_accepts_reformatted_cursor_edit_via_sha_delta(tmp_path):
    """When Cursor reformats output, sha delta still confirms the edit."""
    old_block = (
        "    setup_jinja(app, loader=PackageLoader('sqli', 'templates'),\n"
        "                context_processors=[csrf_processor, auth_user_processor],\n"
        "                autoescape=False)\n"
    )
    old_file = (
        "from pkg import app\n"
        "from pkg.loader import PackageLoader\n\n"
        f"{old_block}"
        "    setup_database(app)\n"
    )
    target = tmp_path / "sqli" / "app.py"
    target.parent.mkdir(parents=True)
    target.write_text(old_file, encoding="utf-8")

    planned_new_file = old_file.replace("autoescape=False", "autoescape=True")

    class ReformattedGateway(LocalPatchCursorGateway):
        def edit(self, instruction, *, files=None, fix_plan=None, metadata=None):
            meta = metadata or {}
            rel = (files or ["sqli/app.py"])[0]
            fp = self.cwd / rel
            text = fp.read_text(encoding="utf-8")
            old_s = meta.get("old_string") or ""
            if old_s not in text:
                return CursorEditResult(status="failed", text="old missing", exit_code=1)
            reformatted = text.replace(
                old_block,
                (
                    "    setup_jinja(\n"
                    "        app,\n"
                    "        loader=PackageLoader('sqli', 'templates'),\n"
                    "        context_processors=[csrf_processor, auth_user_processor],\n"
                    "        autoescape=True,\n"
                    "    )\n"
                ),
                1,
            )
            fp.write_text(reformatted, encoding="utf-8")
            return CursorEditResult(
                status="success",
                text="reformatted patch",
                exit_code=0,
                changed_files=[rel],
            )

    plan = ExecutionPlan(
        ticket_id="SPACE-129069",
        root_hypothesis="Enable Jinja autoescape",
        files_to_change=["sqli/app.py"],
        execution_steps=[
            ExecutionStep(
                step_id=2,
                action="EDIT",
                where="sqli/app.py",
                what="Enable autoescape",
                why="XSS fix",
                validate="autoescape=True",
                old_string=old_file,
                new_string=planned_new_file,
            ),
        ],
        risk_level="medium",
    )
    mock_llm = MagicMock()
    registry, _ = build_executor_tools(str(tmp_path))
    try:
        ok, out, _devs = _execute_edit(
            registry,
            plan.execution_steps[0].model_dump(),
            plan,
            create_files=False,
            llm=mock_llm,
            gateway=ReformattedGateway(tmp_path),
        )
    finally:
        registry.dispose()
    assert ok, out
    mock_llm.invoke.assert_not_called()
    written = target.read_text(encoding="utf-8")
    assert "autoescape=True" in written
    assert "autoescape=False" not in written
    assert sha256_text(written) != sha256_text(planned_new_file)
