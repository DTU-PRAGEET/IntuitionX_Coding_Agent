"""Git worktree management for isolated execution."""

from __future__ import annotations

import re
import shutil
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
    """Remote Branch Exists for this module."""
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
    """Current Branch for this module."""
    result = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_path, check=False)
    if result.returncode != 0:
        return None
    name = (result.stdout or "").strip()
    return name if name and name != "HEAD" else None


def _run_git(args: list[str], cwd: str, check: bool = True) -> subprocess.CompletedProcess:
    """Run Git for this module."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


# --- M1.3 commented out: validate_repository (see docs/cleanup-audit.md) ---
# def validate_repository(repo_path: str) -> tuple[bool, str]:
#     """Validate repository."""
#     p = Path(repo_path)
#     if not p.exists():
#         return False, f"Repository path does not exist: {repo_path}"
#     if not (p / ".git").exists() and not _run_git(["rev-parse", "--git-dir"], str(p), check=False).returncode == 0:
#         return False, f"Not a git repository: {repo_path}"
#     return True, "ok"
# --- end M1.3: validate_repository ---


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


def _is_git_checkout(path: str) -> bool:
    """True when path is a checkout with a resolvable HEAD."""
    return bool(git_repo_snapshot(path).get("is_git"))


def _git_common_dir(repo_path: str) -> str | None:
    """Resolved git-common-dir for a checkout, or None if unavailable."""
    result = _run_git(["rev-parse", "--git-common-dir"], repo_path, check=False)
    if result.returncode != 0:
        return None
    raw = (result.stdout or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = Path(repo_path) / path
    try:
        return str(path.resolve())
    except OSError:
        return None


def _worktree_belongs_to_source(worktree_path: str, source_repo_path: str) -> bool:
    """True when the worktree shares the source repo's object database."""
    worktree_common = _git_common_dir(worktree_path)
    source_common = _git_common_dir(source_repo_path)
    if not worktree_common or not source_common:
        return False
    return worktree_common == source_common


def _remove_worktree_dir(worktree_path: Path, source_repo_path: str) -> None:
    """Unregister a worktree from git and delete its directory."""
    if not worktree_path.exists():
        return
    _log.warning("worktree_remove", worktree_path=str(worktree_path))
    # Prefer the worktree's real owner — stale ticket dirs may belong to a
    # different ticket_repos cache than the current source_repo_path.
    owner_common = _git_common_dir(str(worktree_path))
    source_common = _git_common_dir(source_repo_path)
    if owner_common and owner_common != source_common:
        subprocess.run(
            ["git", "--git-dir", owner_common, "worktree", "remove", "--force", str(worktree_path)],
            capture_output=True,
            text=True,
            check=False,
        )
        subprocess.run(
            ["git", "--git-dir", owner_common, "worktree", "prune"],
            capture_output=True,
            text=True,
            check=False,
        )
    _run_git(
        ["worktree", "remove", "--force", str(worktree_path)],
        source_repo_path,
        check=False,
    )
    _run_git(["worktree", "prune"], source_repo_path, check=False)
    if worktree_path.exists():
        shutil.rmtree(worktree_path, ignore_errors=True)


def _worktree_is_dirty(worktree_path: str) -> bool:
    """Worktree Is Dirty for this module."""
    status = _run_git(["status", "--porcelain"], worktree_path, check=False)
    return bool((status.stdout or "").strip())


def _reset_worktree(worktree_path: str, target_ref: str) -> bool:
    """Hard-reset worktree to target_ref. Returns False when reset fails."""
    _log.info("worktree_reset", worktree_path=worktree_path, target_ref=target_ref)
    reset = _run_git(["reset", "--hard", target_ref], worktree_path, check=False)
    if reset.returncode != 0:
        detail = (reset.stderr or reset.stdout or "reset failed").strip()
        _log.warning(
            "worktree_reset_failed",
            worktree_path=worktree_path,
            target_ref=target_ref,
            detail=detail,
        )
        return False
    _run_git(["clean", "-fd"], worktree_path, check=False)
    return True


def _start_ref(
    repo_path: str,
    *,
    planner_head: str | None,
    base_branch: str,
) -> str:
    """Start Ref for this module."""
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
    """Ensure worktree matches planner snapshot; return status token.

    Status tokens:
    - ``reused`` / ``reset`` — safe to keep this directory
    - ``invalid`` / ``wrong_source`` / ``reset_failed`` — caller must recreate
    """
    snap = git_repo_snapshot(worktree_path)
    if not snap["is_git"]:
        return "invalid"

    if not _worktree_belongs_to_source(worktree_path, source_repo_path):
        _log.warning(
            "worktree_source_mismatch",
            worktree_path=worktree_path,
            source_repo_path=source_repo_path,
            worktree_common=_git_common_dir(worktree_path),
            source_common=_git_common_dir(source_repo_path),
        )
        return "wrong_source"

    dirty = _worktree_is_dirty(worktree_path)
    head_mismatch = bool(planner_head and snap["head"] and snap["head"] != planner_head)
    head_missing = bool(planner_head and not snap["head"])

    if not dirty and not head_mismatch and not head_missing:
        return "reused"

    target = _start_ref(
        source_repo_path,
        planner_head=planner_head,
        base_branch=base_branch,
    )

    if not _reset_worktree(worktree_path, target):
        return "reset_failed"

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
        return "reset_failed"
    return "reset"


def _checkout_new_branch(worktree_path: str, branch_name: str) -> None:
    """Checkout New Branch for this module."""
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

    _RECREATE_STATUSES = frozenset({"invalid", "wrong_source", "reset_failed"})

    if worktree_path.exists():
        if not _is_git_checkout(str(worktree_path)):
            _remove_worktree_dir(worktree_path, repo_path)
        else:
            status = _sync_worktree_to_snapshot(
                str(worktree_path),
                planner_head=planner_snapshot_head,
                source_repo_path=repo_path,
                base_branch=base,
            )
            if status in _RECREATE_STATUSES:
                _log.warning(
                    "worktree_recreate",
                    worktree_path=str(worktree_path),
                    reason=status,
                    source_repo_path=repo_path,
                )
                _remove_worktree_dir(worktree_path, repo_path)
            else:
                _checkout_new_branch(str(worktree_path), branch_name)
                if not _is_git_checkout(str(worktree_path)):
                    _remove_worktree_dir(worktree_path, repo_path)
                else:
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

    if not _is_git_checkout(str(worktree_path)):
        detail = ""
        if planner_snapshot_head:
            detail = f" (planner HEAD {planner_snapshot_head[:12]})"
        raise RuntimeError(
            f"Failed to create git worktree at {worktree_path}{detail}. "
            "Remove the directory or fix the source repository and retry."
        )

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
    """Stage Commit Push for this module."""
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
    """Show Diff for this module."""
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
