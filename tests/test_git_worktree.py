"""Tests for git worktree helpers."""

from __future__ import annotations

from unittest.mock import patch

from src.agents.executor.tools.git_worktree import (
    create_worktree,
    resolve_base_branch,
    stage_commit_push,
)


def test_resolve_base_branch_prefers_existing_remote(tmp_path):
    with patch("src.agents.executor.tools.git_worktree._remote_branch_exists") as exists:
        exists.side_effect = lambda _path, branch: branch == "main"
        assert resolve_base_branch(str(tmp_path), preferred="develop") == "main"


def test_reused_worktree_resets_when_dirty(tmp_path):
    worktree = tmp_path / "space_129068"
    worktree.mkdir()
    with patch("src.agents.executor.tools.git_worktree._run_git") as mock_git, patch(
        "src.agents.executor.tools.git_worktree._current_branch",
        return_value="fix/space-129068",
    ), patch(
        "src.agents.executor.tools.git_worktree.resolve_base_branch",
        return_value="main",
    ), patch(
        "src.agents.executor.tools.git_worktree._worktree_is_dirty",
        return_value=True,
    ), patch(
        "src.agents.executor.tools.git_worktree.git_repo_snapshot",
        side_effect=[
            {"head": "abc123", "branch": "fix/x", "status_short": " M f.py", "repo_path": str(worktree), "cwd": "", "diff_stat": "", "is_git": True},
            {"head": "def456", "branch": "fix/x", "status_short": "", "repo_path": str(worktree), "cwd": "", "diff_stat": "", "is_git": True},
        ],
    ):
        mock_git.return_value.returncode = 0
        mock_git.return_value.stdout = ""
        info = create_worktree(str(tmp_path), "SPACE-129068", planner_snapshot_head="def456")
    assert info["status"] == "reset"


def test_stage_commit_push_reports_no_changes_without_remote_branch(tmp_path):
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
