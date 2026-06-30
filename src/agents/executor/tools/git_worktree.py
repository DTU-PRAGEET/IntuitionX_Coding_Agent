"""Git worktree management for isolated execution."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from src.core.config import settings
from src.core.logging import get_logger
from src.core.repo_snapshot import git_repo_snapshot, log_repo_snapshot

_log = get_logger("git_worktree")

PROTECTED_BRANCHES = {"main", "master", "develop", "production"}
_BRANCH_MAX_LEN = 63


def _remote_branch_exists(repo_path: str, branch: str) -> bool:
    result = _run_git(["ls-remote", "--heads", "origin", branch], repo_path, check=False)
    return bool((result.stdout or "").strip())


def resolve_base_branch(repo_path: str, preferred: str | None = None) -> str:
    """Pick a base branch that exists on origin, preferring config then remote default."""
    preferred = preferred or settings.git_target_branch
    if _remote_branch_exists(repo_path, preferred):
        return preferred

    default_ref = _run_git(["symbolic-ref", "refs/remotes/origin/HEAD"], repo_path, check=False)
    if default_ref.returncode == 0:
        remote_default = (default_ref.stdout or "").strip().rsplit("/", 1)[-1]
        if remote_default and _remote_branch_exists(repo_path, remote_default):
            return remote_default

    for fallback in ("main", "master", "develop"):
        if _remote_branch_exists(repo_path, fallback):
            return fallback
    return preferred


def _current_branch(repo_path: str) -> str | None:
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path, check=False)
    if result.returncode != 0:
        return None
    name = (result.stdout or "").strip()
    return name if name and name != "HEAD" else None


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
    """Build a unique fix branch name — each pipeline run gets its own branch."""
    slug = re.sub(r"[^a-z0-9]+", "-", description.lower())[:20].strip("-")
    run_tag = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    base = f"{prefix}/{ticket_id.lower()}"
    if slug:
        base = f"{base}-{slug}"
    # Reserve room for -{run_tag} so every run gets a distinct branch on origin.
    max_base_len = _BRANCH_MAX_LEN - len(run_tag) - 1
    base = base[:max_base_len].rstrip("-")
    return f"{base}-{run_tag}"


def _worktree_is_dirty(worktree_path: str) -> bool:
    status = _run_git(["status", "--porcelain"], worktree_path, check=False)
    return bool((status.stdout or "").strip())


def _reset_worktree(worktree_path: str, target_ref: str) -> None:
    _log.info("worktree_reset", worktree_path=worktree_path, target_ref=target_ref)
    _run_git(["reset", "--hard", target_ref], worktree_path, check=False)
    _run_git(["clean", "-fd"], worktree_path, check=False)


def _start_ref(
    repo_path: str,
    *,
    planner_head: str | None,
    base_branch: str,
) -> str:
    if planner_head:
        return planner_head
    _run_git(["fetch", "origin", base_branch], repo_path, check=False)
    return f"origin/{base_branch}"


def _sync_worktree_to_snapshot(
    worktree_path: str,
    *,
    planner_head: str | None,
    source_repo_path: str,
    base_branch: str,
) -> str:
    """Ensure worktree matches planner snapshot; return status token."""
    snap = git_repo_snapshot(worktree_path)
    dirty = _worktree_is_dirty(worktree_path)
    head_mismatch = bool(planner_head and snap["head"] and snap["head"] != planner_head)

    if not dirty and not head_mismatch:
        return "reused"

    target = _start_ref(
        source_repo_path,
        planner_head=planner_head,
        base_branch=base_branch,
    )

    _reset_worktree(worktree_path, target)
    after = git_repo_snapshot(worktree_path)
    log_repo_snapshot(
        _log,
        "worktree_after_reset",
        worktree_path,
        previous_head=snap["head"],
        target_ref=target,
        dirty_before=dirty,
        head_mismatch=head_mismatch,
    )
    if planner_head and after["head"] != planner_head:
        _log.warning(
            "worktree_head_still_mismatch",
            expected=planner_head,
            actual=after["head"],
        )
    return "reset"


def _checkout_new_branch(worktree_path: str, branch_name: str) -> None:
    _run_git(["checkout", "-B", branch_name], worktree_path, check=False)


def create_worktree(
    repo_path: str,
    ticket_id: str,
    base_branch: str | None = None,
    description: str = "",
    prefix: str = "fix",
    *,
    planner_snapshot_head: str | None = None,
) -> dict:
    """Create isolated git worktree and a fresh execution branch for this run."""
    base = resolve_base_branch(repo_path, base_branch)
    log_repo_snapshot(_log, "worktree_source", repo_path, ticket_id=ticket_id, base_branch=base)

    branch_name = make_branch_name(ticket_id, prefix, description)
    worktree_base = Path(settings.worktree_base)
    worktree_base.mkdir(parents=True, exist_ok=True)
    worktree_path = worktree_base / ticket_id.lower().replace("-", "_")

    if worktree_path.exists():
        status = _sync_worktree_to_snapshot(
            str(worktree_path),
            planner_head=planner_snapshot_head,
            source_repo_path=repo_path,
            base_branch=base,
        )
        _checkout_new_branch(str(worktree_path), branch_name)
        log_repo_snapshot(
            _log,
            "worktree_ready",
            str(worktree_path),
            status=status,
            branch_name=branch_name,
        )
        return {
            "worktree_path": str(worktree_path),
            "branch_name": branch_name,
            "base_branch": base,
            "status": status,
            "source_repo_path": str(Path(repo_path).resolve()),
            "planner_snapshot_head": planner_snapshot_head,
        }

    start_ref = _start_ref(
        repo_path,
        planner_head=planner_snapshot_head,
        base_branch=base,
    )
    _run_git(
        ["worktree", "add", "-b", branch_name, str(worktree_path), start_ref],
        repo_path,
        check=False,
    )
    if not worktree_path.exists():
        _run_git(["worktree", "add", "-b", branch_name, str(worktree_path)], repo_path)

    log_repo_snapshot(_log, "worktree_created", str(worktree_path), branch_name=branch_name)
    return {
        "worktree_path": str(worktree_path),
        "branch_name": branch_name,
        "base_branch": base,
        "status": "created",
        "source_repo_path": str(Path(repo_path).resolve()),
        "planner_snapshot_head": planner_snapshot_head,
    }


def stage_commit_push(worktree_path: str, branch_name: str, commit_msg: str) -> dict[str, str | bool]:
    _run_git(["add", "-A"], worktree_path)
    status = _run_git(["status", "--porcelain"], worktree_path, check=False)
    if not (status.stdout or "").strip():
        on_remote = _remote_branch_exists(worktree_path, branch_name)
        return {
            "status": "no_changes",
            "pushed": False,
            "branch_on_remote": on_remote,
            "message": "No changes to commit",
        }
    commit = _run_git(["commit", "-m", commit_msg], worktree_path, check=False)
    if commit.returncode != 0:
        detail = (commit.stderr or commit.stdout or "commit failed").strip()
        return {"status": "commit_failed", "pushed": False, "message": detail}
    push = _run_git(["push", "-u", "origin", branch_name], worktree_path, check=False)
    if push.returncode != 0:
        detail = (push.stderr or push.stdout or "push failed").strip()
        return {"status": "push_failed", "pushed": False, "message": detail}
    return {
        "status": "ok",
        "pushed": True,
        "branch_on_remote": True,
        "message": "committed and pushed",
    }


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
