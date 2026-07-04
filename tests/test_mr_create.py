"""Tests for GitHub PR creation helpers."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from src.agents.executor.tools.mr_create import (
    github_repo_from_ticket,
    parse_github_owner_repo,
    resolve_github_repo,
)


def test_parse_github_owner_repo_from_url():
    """Test Parse Github Owner Repo From Url for this module."""
    assert (
        parse_github_owner_repo("https://github.com/DTU-PRAGEET/Testing-Security-demo#")
        == "DTU-PRAGEET/Testing-Security-demo"
    )


def test_github_repo_from_ticket_prefers_valid_link():
    """Test Github Repo From Ticket Prefers Valid Link for this module."""
    ticket = {
        "issue_id": "SCRUM-5",
        "repo_links": ["com/DTU-PRAGEET/Testing-Security-demo"],
        "problem_statement": (
            "Repository Link:\n"
            "[https://github.com/DTU-PRAGEET/Testing-Security-demo#|smart-link]"
        ),
    }
    assert github_repo_from_ticket(ticket) == "DTU-PRAGEET/Testing-Security-demo"


def test_resolve_github_repo_from_remote(tmp_path):
    """Test Resolve Github Repo From Remote for this module."""
    with patch("src.agents.executor.tools.mr_create._run_git") as mock_git:
        mock_git.return_value.returncode = 0
        mock_git.return_value.stdout = "https://github.com/acme/demo.git\n"
        assert resolve_github_repo(str(tmp_path)) == "acme/demo"


def test_preflight_rejects_missing_head_branch(tmp_path):
    """Test Preflight Rejects Missing Head Branch for this module."""
    from src.agents.executor.tools import mr_create as mr_mod

    with patch.object(mr_mod, "_github_request", return_value={}), patch.object(
        mr_mod, "_remote_branch_exists", return_value=False
    ):
        with pytest.raises(RuntimeError, match="Head branch 'feature' is not on origin"):
            mr_mod._preflight_github_pr(str(tmp_path), "acme/demo", "feature", "main")
