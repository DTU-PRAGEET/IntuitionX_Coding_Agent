"""Tests for git worktree helpers."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.agents.executor.tools import git_worktree as gw_mod
from src.agents.executor.tools.git_worktree import (
    create_worktree,
    make_branch_name,
    resolve_base_branch,
    stage_commit_push,
)


def test_resolve_base_branch_prefers_existing_remote(tmp_path):
    """Test Resolve Base Branch Prefers Existing Remote for this module."""
    with patch("src.agents.executor.tools.git_worktree._remote_branch_exists") as exists:
        exists.side_effect = lambda _path, branch: branch == "main"
        assert resolve_base_branch(str(tmp_path), preferred="develop") == "main"


def test_make_branch_name_includes_run_timestamp():
    """Test Make Branch Name Includes Run Timestamp for this module."""
    import re

    name = make_branch_name("SPACE-129069", description="stored reflected xss")
    assert name.startswith("fix/space-129069-stored-reflected-x")
    assert re.search(r"-\d{8}-\d{4}$", name)


def test_reused_worktree_checks_out_new_branch(tmp_path):
    """Test Reused Worktree Checks Out New Branch for this module."""
    worktree_root = tmp_path / "worktrees"
    worktree = worktree_root / "space_129068"
    worktree.mkdir(parents=True)
    patched_settings = SimpleNamespace(**{**vars(gw_mod.settings), "worktree_base": str(worktree_root)})
    with patch("src.agents.executor.tools.git_worktree.settings", patched_settings), patch(
        "src.agents.executor.tools.git_worktree._run_git",
    ) as mock_git, patch(
        "src.agents.executor.tools.git_worktree.resolve_base_branch",
        return_value="main",
    ), patch(
        "src.agents.executor.tools.git_worktree._worktree_belongs_to_source",
        return_value=True,
    ), patch(
        "src.agents.executor.tools.git_worktree._worktree_is_dirty",
        return_value=True,
    ), patch(
        "src.agents.executor.tools.git_worktree._is_git_checkout",
        return_value=True,
    ), patch(
        "src.agents.executor.tools.git_worktree.git_repo_snapshot",
        side_effect=[
            {"head": "abc123", "branch": "fix/x", "status_short": " M f.py", "repo_path": str(worktree), "cwd": "", "diff_stat": "", "is_git": True},
            {"head": "def456", "branch": "fix/x", "status_short": "", "repo_path": str(worktree), "cwd": "", "diff_stat": "", "is_git": True},
        ],
    ), patch(
        "src.agents.executor.tools.git_worktree.make_branch_name",
        return_value="fix/space-129068-20260629-1800",
    ):
        mock_git.return_value.returncode = 0
        mock_git.return_value.stdout = ""
        info = create_worktree(str(tmp_path), "SPACE-129068", planner_snapshot_head="def456")

    assert info["status"] == "reset"
    assert info["branch_name"] == "fix/space-129068-20260629-1800"
    checkout_calls = [
        call.args[0]
        for call in mock_git.call_args_list
        if call.args and call.args[0][:2] == ["checkout", "-B"]
    ]
    assert checkout_calls == [["checkout", "-B", "fix/space-129068-20260629-1800"]]


def test_reused_worktree_recreated_when_wrong_source(tmp_path):
    """Stale worktree from a different ticket_repos cache must be replaced."""
    worktree_root = tmp_path / "worktrees"
    worktree = worktree_root / "space_129072"
    worktree.mkdir(parents=True)
    (worktree / "README.md").write_text("stale\n", encoding="utf-8")

    patched_settings = SimpleNamespace(**{**vars(gw_mod.settings), "worktree_base": str(worktree_root)})
    with patch("src.agents.executor.tools.git_worktree.settings", patched_settings), patch(
        "src.agents.executor.tools.git_worktree._run_git",
    ) as mock_git, patch(
        "src.agents.executor.tools.git_worktree.resolve_base_branch",
        return_value="main",
    ), patch(
        "src.agents.executor.tools.git_worktree.make_branch_name",
        return_value="fix/space-129072-20260716-1300",
    ), patch(
        "src.agents.executor.tools.git_worktree._is_git_checkout",
        side_effect=[True, True],
    ), patch(
        "src.agents.executor.tools.git_worktree._worktree_belongs_to_source",
        return_value=False,
    ), patch(
        "src.agents.executor.tools.git_worktree.git_repo_snapshot",
        return_value={
            "head": "1999f8c",
            "branch": "fix/old",
            "status_short": "",
            "repo_path": str(worktree),
            "cwd": "",
            "diff_stat": "",
            "is_git": True,
        },
    ), patch(
        "src.agents.executor.tools.git_worktree._remove_worktree_dir",
    ) as mock_remove:
        mock_git.return_value.returncode = 0
        mock_git.return_value.stdout = ""
        # After remove, directory is gone so create path runs worktree add.
        mock_remove.side_effect = lambda path, _src: path.exists() and __import__("shutil").rmtree(path)
        info = create_worktree(
            str(tmp_path / "DTU-PRAGEET__juice-shop"),
            "SPACE-129072",
            planner_snapshot_head="d9e533960bccf5d20d37f9d6422a7114ed8abe6b",
        )

    assert info["status"] == "created"
    mock_remove.assert_called_once()
    worktree_add_calls = [
        call.args[0]
        for call in mock_git.call_args_list
        if call.args and call.args[0][:2] == ["worktree", "add"]
    ]
    assert worktree_add_calls
    assert worktree_add_calls[0][-1] == "d9e533960bccf5d20d37f9d6422a7114ed8abe6b"


def test_reused_worktree_recreated_when_reset_fails(tmp_path):
    """Failed reset --hard to planner HEAD must recreate, not keep stale HEAD."""
    worktree_root = tmp_path / "worktrees"
    worktree = worktree_root / "space_129073"
    worktree.mkdir(parents=True)

    patched_settings = SimpleNamespace(**{**vars(gw_mod.settings), "worktree_base": str(worktree_root)})
    with patch("src.agents.executor.tools.git_worktree.settings", patched_settings), patch(
        "src.agents.executor.tools.git_worktree._run_git",
    ) as mock_git, patch(
        "src.agents.executor.tools.git_worktree.resolve_base_branch",
        return_value="main",
    ), patch(
        "src.agents.executor.tools.git_worktree.make_branch_name",
        return_value="fix/space-129073-20260716-1300",
    ), patch(
        "src.agents.executor.tools.git_worktree._is_git_checkout",
        side_effect=[True, True],
    ), patch(
        "src.agents.executor.tools.git_worktree._worktree_belongs_to_source",
        return_value=True,
    ), patch(
        "src.agents.executor.tools.git_worktree._worktree_is_dirty",
        return_value=False,
    ), patch(
        "src.agents.executor.tools.git_worktree._reset_worktree",
        return_value=False,
    ), patch(
        "src.agents.executor.tools.git_worktree.git_repo_snapshot",
        return_value={
            "head": "1999f8c",
            "branch": "fix/old",
            "status_short": "",
            "repo_path": str(worktree),
            "cwd": "",
            "diff_stat": "",
            "is_git": True,
        },
    ), patch(
        "src.agents.executor.tools.git_worktree._remove_worktree_dir",
    ) as mock_remove:
        mock_git.return_value.returncode = 0
        mock_git.return_value.stdout = ""
        mock_remove.side_effect = lambda path, _src: path.exists() and __import__("shutil").rmtree(path)
        info = create_worktree(
            str(tmp_path),
            "SPACE-129073",
            planner_snapshot_head="d9e533960bcc",
        )

    assert info["status"] == "created"
    mock_remove.assert_called_once()


def test_reused_worktree_recreated_when_not_git(tmp_path):
    """A leftover non-git directory must be removed and replaced with a real worktree."""
    worktree_root = tmp_path / "worktrees"
    worktree = worktree_root / "space_129070"
    worktree.mkdir(parents=True)
    (worktree / "README.md").write_text("stale checkout\n", encoding="utf-8")

    patched_settings = SimpleNamespace(**{**vars(gw_mod.settings), "worktree_base": str(worktree_root)})
    with patch("src.agents.executor.tools.git_worktree.settings", patched_settings), patch(
        "src.agents.executor.tools.git_worktree._run_git",
    ) as mock_git, patch(
        "src.agents.executor.tools.git_worktree.resolve_base_branch",
        return_value="main",
    ), patch(
        "src.agents.executor.tools.git_worktree.make_branch_name",
        return_value="fix/space-129070-20260707-0900",
    ), patch(
        "src.agents.executor.tools.git_worktree._is_git_checkout",
        side_effect=[False, True],
    ):
        mock_git.return_value.returncode = 0
        mock_git.return_value.stdout = ""
        info = create_worktree(
            str(tmp_path),
            "SPACE-129070",
            planner_snapshot_head="3f6156bf12206535ccb7e2da533c4a0a49ef7b19",
        )

    assert info["status"] == "created"
    assert info["branch_name"] == "fix/space-129070-20260707-0900"
    worktree_add_calls = [
        call.args[0]
        for call in mock_git.call_args_list
        if call.args and call.args[0][:2] == ["worktree", "add"]
    ]
    assert worktree_add_calls


def test_stage_commit_push_reports_no_changes_without_remote_branch(tmp_path):
    """Test Stage Commit Push Reports No Changes Without Remote Branch for this module."""
    with patch("src.agents.executor.tools.git_worktree._run_git") as mock_git, patch(
        "src.agents.executor.tools.git_worktree._remote_branch_exists",
        return_value=False,
    ):
        mock_git.return_value.returncode = 0
        mock_git.return_value.stdout = ""
        result = stage_commit_push(str(tmp_path), "fix/demo", "msg")
    assert result["status"] == "no_changes"
    assert result["pushed"] is False
    assert result["branch_on_remote"] is False


def test_worktree_belongs_to_source_compares_common_dirs(tmp_path):
    """Belonging is decided by shared git-common-dir, not ticket folder name."""
    from src.agents.executor.tools.git_worktree import _worktree_belongs_to_source

    source = tmp_path / "DTU-PRAGEET__juice-shop"
    other = tmp_path / "juice-shop__juice-shop"
    worktree = tmp_path / "space_129072"
    source.mkdir()
    other.mkdir()
    worktree.mkdir()

    def fake_common(path: str) -> str | None:
        resolved = str(Path(path).resolve())
        if resolved == str(source.resolve()) or resolved == str(worktree.resolve()):
            return str((source / ".git").resolve())
        if resolved == str(other.resolve()):
            return str((other / ".git").resolve())
        return None

    with patch(
        "src.agents.executor.tools.git_worktree._git_common_dir",
        side_effect=fake_common,
    ):
        assert _worktree_belongs_to_source(str(worktree), str(source)) is True
        assert _worktree_belongs_to_source(str(worktree), str(other)) is False