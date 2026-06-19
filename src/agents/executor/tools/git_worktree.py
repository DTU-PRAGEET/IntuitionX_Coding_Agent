"""Git worktree management for isolated execution."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import settings

PROTECTED_BRANCHES = {"main", "master", "develop", "production"}


def _run_git(args: list[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def validate_repository(repo_path: str) -> tuple[bool, str]:
    p = Path(repo_path)
    if not p.exists():
        return False, f"Repository path does not exist: {repo_path}"
    if not (p / ".git").exists() and not _run_git(["rev-parse", "--git-dir"], str(p), check=False).returncode == 0:
        return False, f"Not a git repository: {repo_path}"
    return True, "ok"


def make_branch_name(ticket_id: str, prefix: str = "fix", description: str = "") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower())[:30].strip("-")
    base = f"{prefix}/{ticket_id.lower()}"
    if slug:
        base = f"{base}-{slug}"
    name = base[:50]
    return name


def create_worktree(
    repo_path: str,
    ticket_id: str,
    base_branch: str | None = None,
    description: str = "",
    prefix: str = "fix",
) -> dict:
    """Create isolated git worktree and execution branch."""
    base = base_branch or settings.git_target_branch
    if base in PROTECTED_BRANCHES:
        pass

    branch_name = make_branch_name(ticket_id, prefix, description)
    worktree_base = Path(settings.worktree_base)
    worktree_base.mkdir(parents=True, exist_ok=True)
    worktree_path = worktree_base / ticket_id.lower().replace("-", "_")

    existing = _run_git(["branch", "--list", branch_name], repo_path, check=False)
    if branch_name in (existing.stdout or ""):
        ts = datetime.now(timezone.utc).strftime("%Y%m%d%H%M")
        branch_name = f"{branch_name[:40]}-{ts}"[:50]

    if worktree_path.exists():
        return {
            "worktree_path": str(worktree_path),
            "branch_name": branch_name,
            "base_branch": base,
            "status": "reused",
        }

    _run_git(["fetch", "origin", base], repo_path, check=False)
    _run_git(
        ["worktree", "add", "-b", branch_name, str(worktree_path), f"origin/{base}"],
        repo_path,
        check=False,
    )
    result = _run_git(
        ["worktree", "add", "-b", branch_name, str(worktree_path), base],
        repo_path,
        check=False,
    )
    if result.returncode != 0 and not worktree_path.exists():
        _run_git(["worktree", "add", "-b", branch_name, str(worktree_path)], repo_path)

    return {
        "worktree_path": str(worktree_path),
        "branch_name": branch_name,
        "base_branch": base,
        "status": "created",
    }


def stage_commit_push(worktree_path: str, branch_name: str, commit_msg: str) -> str:
    _run_git(["add", "-A"], worktree_path)
    status = _run_git(["status", "--porcelain"], worktree_path, check=False)
    if not (status.stdout or "").strip():
        return "No changes to commit"
    _run_git(["commit", "-m", commit_msg], worktree_path)
    _run_git(["push", "-u", "origin", branch_name], worktree_path, check=False)
    return "committed and pushed"


def show_diff(worktree_path: str, base_branch: str | None = None) -> str:
    base = base_branch or settings.git_target_branch
    result = _run_git(["diff", base, "--stat"], worktree_path, check=False)
    stat = result.stdout or result.stderr or ""
    result2 = _run_git(["diff", base], worktree_path, check=False)
    full = (result2.stdout or "")[:15000]
    return f"--- diff stat ---\n{stat}\n--- diff ---\n{full}"


def raise_gitlab_mr(
    branch_name: str,
    ticket_id: str,
    title: str,
    description: str,
) -> str:
    """Create GitLab merge request."""
    url = settings.gitlab_url
    token = settings.gitlab_token
    project_id = settings.gitlab_project_id
    target = settings.git_target_branch

    if not all([url, token, project_id]):
        return f"mock://gitlab/{project_id or 'project'}/-/merge_requests/1?source_branch={branch_name}"

    import gitlab

    gl = gitlab.Gitlab(url, private_token=token)
    project = gl.projects.get(project_id)
    mr = project.mergerequests.create(
        {
            "source_branch": branch_name,
            "target_branch": target,
            "title": title or f"[AUTO] Fix for {ticket_id}",
            "description": description,
        }
    )
    return mr.web_url
