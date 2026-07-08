"""Tests for ticket repository resolution."""

from __future__ import annotations

from unittest.mock import patch

from src.core.ticket_repo import (
    _checkout_healthy,
    _sync_cached_checkout,
    ensure_github_checkout,
    resolve_ticket_repo_path,
    ticket_github_repo,
    ticket_target_branch,
)
from tests.test_jira_repo_links import SCRUM_5_DESCRIPTION


def _git_ok(*_args, **_kwargs):
    result = type("GitResult", (), {"returncode": 0, "stdout": "", "stderr": ""})()
    return result


def _git_fail(*_args, **_kwargs):
    result = type("GitResult", (), {"returncode": 128, "stdout": "", "stderr": "fatal"})()
    return result


def _scrum_ticket() -> dict:
    """Scrum Ticket for this module."""
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
    """Test Ticket Github Repo From Description for this module."""
    ticket = _scrum_ticket()
    assert ticket_github_repo(ticket) == "DTU-PRAGEET/Testing-Security-demo"
    assert ticket_target_branch(ticket) == "master"


def test_resolve_ticket_repo_clones_when_no_explicit_path(tmp_path):
    """Test Resolve Ticket Repo Clones When No Explicit Path for this module."""
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
    """Test Resolve Ticket Repo Honors Explicit Override for this module."""
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


def test_checkout_healthy_false_when_head_missing(tmp_path):
    """Corrupt cache shells (missing HEAD) must not pass health checks."""
    dest = tmp_path / "broken"
    dest.mkdir()

    with patch("src.core.ticket_repo._run_git", side_effect=_git_fail):
        assert _checkout_healthy(dest) is False


def test_checkout_healthy_false_when_tracked_files_missing(tmp_path):
    """HEAD may resolve while the working tree is empty after worktree corruption."""

    def _git_ls_files_missing(_args, cwd=None):
        del cwd
        if _args[:2] == ["rev-parse", "HEAD"]:
            return type("GitResult", (), {"returncode": 0, "stdout": "abc\n", "stderr": ""})()
        if _args[:1] == ["ls-files"]:
            return type(
                "GitResult",
                (),
                {"returncode": 0, "stdout": "README.md\n", "stderr": ""},
            )()
        return _git_ok()

    dest = tmp_path / "empty-tree"
    dest.mkdir()

    with patch("src.core.ticket_repo._run_git", side_effect=_git_ls_files_missing):
        assert _checkout_healthy(dest) is False


def test_checkout_healthy_true_when_tracked_file_exists(tmp_path):
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "README.md").write_text("hello", encoding="utf-8")

    def _git_with_files(_args, cwd=None):
        del cwd
        if _args[:2] == ["rev-parse", "HEAD"]:
            return type("GitResult", (), {"returncode": 0, "stdout": "abc\n", "stderr": ""})()
        if _args[:1] == ["ls-files"]:
            return type(
                "GitResult",
                (),
                {"returncode": 0, "stdout": "README.md\n", "stderr": ""},
            )()
        return _git_ok()

    with patch("src.core.ticket_repo._run_git", side_effect=_git_with_files):
        assert _checkout_healthy(dest) is True


def test_sync_cached_checkout_materializes_deleted_files(tmp_path):
    """Successful sync ends with a healthy checkout."""
    dest = tmp_path / "repo"
    dest.mkdir()
    (dest / "README.md").write_text("hello", encoding="utf-8")
    git_calls: list[list[str]] = []

    def _record_git(args, cwd=None):
        del cwd
        git_calls.append(list(args))
        return _git_ok()

    with patch("src.core.ticket_repo._run_git", side_effect=_record_git), patch(
        "src.core.ticket_repo.repair_repo_from_cursor_temp_clone",
    ), patch(
        "src.core.ticket_repo._checkout_healthy",
        return_value=True,
    ):
        assert _sync_cached_checkout(dest, branch="master") is True

    assert ["worktree", "prune"] in git_calls
    assert ["fetch", "origin"] in git_calls
    assert ["reset", "--hard", "origin/master"] in git_calls
    assert ["clean", "-fd"] in git_calls


def test_ensure_github_checkout_reclones_corrupt_cache(tmp_path):
    """Broken ticket-repo caches should be deleted and freshly cloned."""
    cache_root = tmp_path / "ticket_repos"
    dest = cache_root / "DTU-PRAGEET__wrongsecrets"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()
    (dest / "src").mkdir()

    def _clone_into(dest_path, remote, branch, owner_repo):
        del remote, branch, owner_repo
        dest_path.mkdir(parents=True, exist_ok=True)
        (dest_path / "src").mkdir(exist_ok=True)
        (dest_path / "src" / "app.txt").write_text("ok", encoding="utf-8")
        (dest_path / ".git").mkdir(exist_ok=True)

    with patch("src.core.ticket_repo.settings") as mock_settings, patch(
        "src.core.ticket_repo._sync_cached_checkout",
        return_value=False,
    ), patch(
        "src.core.ticket_repo._clone_github_repo",
        side_effect=_clone_into,
    ) as mock_clone, patch(
        "src.core.ticket_repo.repair_repo_from_cursor_temp_clone",
    ), patch(
        "src.core.ticket_repo._checkout_healthy",
        return_value=True,
    ):
        mock_settings.worktree_base = str(tmp_path)
        path = ensure_github_checkout("DTU-PRAGEET/wrongsecrets")

    mock_clone.assert_called_once()
    assert path == str(dest.resolve())
    assert (dest / "src" / "app.txt").read_text(encoding="utf-8") == "ok"
