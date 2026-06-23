"""Tests for ticket repository resolution."""

from __future__ import annotations

from unittest.mock import patch

from src.core.ticket_repo import (
    resolve_ticket_repo_path,
    ticket_github_repo,
    ticket_target_branch,
)
from tests.test_jira_repo_links import SCRUM_5_DESCRIPTION


def _scrum_ticket() -> dict:
    return {
        "issue_id": "SCRUM-5",
        "title": "SQL Injection vulnerability in user search endpoint",
        "issue_type": "Security",
        "status": "To Do",
        "priority_raw": "Medium",
        "priority_normalized": "P2",
        "module": "User Search",
        "problem_statement": SCRUM_5_DESCRIPTION,
        "acceptance_criteria": [],
        "assignee": "not_specified",
        "reporter": "not_specified",
        "labels": ["security"],
        "repo_links": ["DTU-PRAGEET/Testing-Security-demo"],
        "dependencies": [],
        "attachments": [],
        "comments": [],
        "ticket_history_summary": {
            "github_repo": "DTU-PRAGEET/Testing-Security-demo",
            "target_branch": "master",
        },
        "flags": [],
    }


def test_ticket_github_repo_from_description():
    ticket = _scrum_ticket()
    assert ticket_github_repo(ticket) == "DTU-PRAGEET/Testing-Security-demo"
    assert ticket_target_branch(ticket) == "master"


def test_resolve_ticket_repo_clones_when_no_explicit_path(tmp_path):
    ticket = _scrum_ticket()
    cached = tmp_path / "ticket_repos" / "DTU-PRAGEET__Testing-Security-demo"
    cached.mkdir(parents=True)
    (cached / ".git").mkdir()

    with patch(
        "src.core.ticket_repo.ensure_github_checkout",
        return_value=str(cached),
    ) as mock_clone:
        repo_path, github_repo, branch = resolve_ticket_repo_path(ticket, explicit_path=None)

    mock_clone.assert_called_once_with("DTU-PRAGEET/Testing-Security-demo", branch="master")
    assert repo_path == str(cached)
    assert github_repo == "DTU-PRAGEET/Testing-Security-demo"
    assert branch == "master"


def test_resolve_ticket_repo_honors_explicit_override(tmp_path):
    ticket = _scrum_ticket()
    explicit = tmp_path / "my-local-clone"
    explicit.mkdir()

    repo_path, github_repo, branch = resolve_ticket_repo_path(
        ticket,
        explicit_path=str(explicit),
    )
    assert repo_path == str(explicit.resolve())
    assert github_repo == "DTU-PRAGEET/Testing-Security-demo"
    assert branch == "master"
