"""Resolve a local checkout path for the repository named in a Jira ticket."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from src.core.config import settings
from src.core.file_paths import repair_repo_from_cursor_temp_clone
from src.core.logging import get_logger
from src.core.state import JiraTicket
from src.core.github_links import extract_target_branch, github_slugs_from_text

_log = get_logger("ticket_repo")

_HEALTH_SAMPLE_LIMIT = 20


def _run_git(args: list[str], cwd: str | None = None) -> subprocess.CompletedProcess:
    """Run Git for this module."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def _checkout_healthy(dest: Path) -> bool:
    """True when HEAD resolves and at least one tracked file exists on disk."""
    dest_s = str(dest)
    if _run_git(["rev-parse", "HEAD"], dest_s).returncode != 0:
        return False

    listed = _run_git(["ls-files"], dest_s)
    if listed.returncode != 0:
        return False

    tracked = [line.strip() for line in (listed.stdout or "").splitlines() if line.strip()]
    if not tracked:
        return False

    for rel in tracked[:_HEALTH_SAMPLE_LIMIT]:
        if (dest / rel).is_file():
            return True
    return False


def _sync_cached_checkout(dest: Path, branch: str | None) -> bool:
    """Refresh a cached checkout and materialize the working tree."""
    dest_s = str(dest)
    repair_repo_from_cursor_temp_clone(dest_s)
    _run_git(["worktree", "prune"], dest_s)
    _run_git(["fetch", "origin"], dest_s)

    if branch:
        checkout = _run_git(["checkout", "-f", branch], dest_s)
        if checkout.returncode != 0:
            _run_git(["checkout", "-f", "-B", branch, f"origin/{branch}"], dest_s)
        reset = _run_git(["reset", "--hard", f"origin/{branch}"], dest_s)
    else:
        reset = _run_git(["reset", "--hard", "HEAD"], dest_s)

    if reset.returncode != 0:
        _run_git(["reset", "--hard", "HEAD"], dest_s)
    _run_git(["clean", "-fd"], dest_s)
    return _checkout_healthy(dest)


def _remove_cache_dir(dest: Path) -> None:
    """Delete a broken ticket-repo cache directory."""
    shutil.rmtree(dest, ignore_errors=True)


def _clone_github_repo(dest: Path, remote: str, branch: str | None, owner_repo: str) -> None:
    """Clone owner/repo into dest; raise RuntimeError on failure."""
    clone_cmd = ["clone"]
    if branch:
        clone_cmd.extend(["--branch", branch, "--single-branch", "--depth", "1"])
    else:
        clone_cmd.extend(["--depth", "1"])
    clone_cmd.extend([remote, str(dest)])
    result = _run_git(clone_cmd)
    if result.returncode != 0:
        if branch:
            result = _run_git(["clone", "--depth", "1", remote, str(dest)])
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "clone failed").strip()
            raise RuntimeError(
                f"Failed to clone GitHub repo {owner_repo!r} (branch={branch!r}): {detail}"
            )


def ticket_github_repo(ticket: JiraTicket) -> str | None:
    """Best GitHub owner/repo slug for a ticket."""
    description = ticket.get("problem_statement") or ""
    slugs = github_slugs_from_text(description)
    if slugs:
        return slugs[0]
    for link in ticket.get("repo_links") or []:
        parsed = github_slugs_from_text(link if "github.com" in link else f"https://github.com/{link}")
        if parsed:
            return parsed[0]
        if "/" in link and " " not in link and len(link) < 120:
            parts = link.strip("/").split("/")
            if len(parts) >= 2 and parts[0] not in {"com", "http", "https"}:
                return f"{parts[-2]}/{parts[-1]}"
    enrichment = (ticket.get("ticket_history_summary") or {}).get("security_enrichment") or {}
    repository = enrichment.get("repository")
    if repository and "/" in repository:
        slugs = github_slugs_from_text(f"https://github.com/{repository}")
        if slugs:
            return slugs[0]
    return None


def ticket_target_branch(ticket: JiraTicket) -> str | None:
    """Target branch from ticket description (e.g. `Branch: master`)."""
    return extract_target_branch(ticket.get("problem_statement") or "")


def ensure_github_checkout(owner_repo: str, branch: str | None = None) -> str:
    """Clone or update a cached checkout for owner/repo; return local path."""
    cache_root = Path(settings.worktree_base) / "ticket_repos"
    cache_root.mkdir(parents=True, exist_ok=True)
    dest = cache_root / owner_repo.replace("/", "__")
    remote = f"https://github.com/{owner_repo}.git"

    if dest.exists() and (dest / ".git").exists():
        if _sync_cached_checkout(dest, branch):
            _log.info("repo_cache_hit", owner_repo=owner_repo, path=str(dest), branch=branch)
            return str(dest.resolve())

        _log.warning(
            "repo_cache_corrupt",
            owner_repo=owner_repo,
            path=str(dest),
            branch=branch,
            action="reclone",
        )
        _remove_cache_dir(dest)

    _clone_github_repo(dest, remote, branch, owner_repo)
    repair_repo_from_cursor_temp_clone(str(dest))

    if not _checkout_healthy(dest):
        detail = "checkout materialized but working tree has no tracked files"
        _remove_cache_dir(dest)
        raise RuntimeError(
            f"GitHub repo {owner_repo!r} checkout at {dest} is unusable after clone: {detail}"
        )

    _log.info("repo_cloned", owner_repo=owner_repo, path=str(dest), branch=branch)
    return str(dest.resolve())


def resolve_ticket_repo_path(
    ticket: JiraTicket,
    explicit_path: str | None = None,
) -> tuple[str | None, str | None, str | None]:
    """Return (local_repo_path, github_owner_repo, target_branch).

    explicit_path wins when the user supplied a local path override.
    Otherwise clone/update the GitHub repo named in the ticket.
    Falls back to REPO_PATH when the ticket has no GitHub link.
    """
    target_branch = ticket_target_branch(ticket)
    github_repo = ticket_github_repo(ticket)

    if explicit_path:
        path = str(Path(explicit_path).resolve())
        _log.info(
            "repo_explicit",
            path=path,
            github_repo=github_repo,
            target_branch=target_branch,
        )
        return path, github_repo, target_branch

    if github_repo:
        local = ensure_github_checkout(github_repo, branch=target_branch)
        return local, github_repo, target_branch

    if settings.default_repo_path and Path(settings.default_repo_path).exists():
        fallback = str(Path(settings.default_repo_path).resolve())
        _log.warning("repo_fallback_default", path=fallback, ticket_id=ticket.get("issue_id"))
        return fallback, None, target_branch

    return None, github_repo, target_branch
