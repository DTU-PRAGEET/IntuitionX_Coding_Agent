"""Resolve a reproducible local checkout for a case's pinned repo snapshot.

Priority:
  1. snap.local_fixture_path (fully self-contained — preferred for CI)
  2. clone snap.url and hard-checkout snap.commit_sha into a cache dir

Cloning is only triggered in live eval modes; offline scoring never calls this.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evals.schema.case import EvalCase, RepoSnapshot

CACHE_ROOT = Path(".cache/eval_repos").resolve()


def _run(cmd: list[str], cwd: Path | None = None) -> None:
    """Run for this module."""
    subprocess.run(cmd, cwd=str(cwd) if cwd else None, check=True,
                   capture_output=True, text=True)


def resolve_snapshot_path(case_id: str, snap: "RepoSnapshot") -> str:
    """Resolve snapshot path."""
    if snap.local_fixture_path:
        p = Path(snap.local_fixture_path).resolve()
        if not p.exists():
            raise FileNotFoundError(f"{case_id}: local_fixture_path does not exist: {p}")
        return str(p)

    if not snap.url:
        raise ValueError(f"{case_id}: no repo.url and no local_fixture_path to resolve")

    CACHE_ROOT.mkdir(parents=True, exist_ok=True)
    sha = snap.commit_sha or snap.target_branch
    dest = CACHE_ROOT / f"{snap.slug or 'repo'}@{sha[:12]}"
    if (dest / ".git").exists():
        _reset_cached_checkout(dest, snap)
        return str(dest)

    _run(["git", "clone", "--no-checkout", snap.url, str(dest)])
    if snap.commit_sha:
        _run(["git", "fetch", "--depth", "1", "origin", snap.commit_sha], cwd=dest)
        _run(["git", "checkout", snap.commit_sha], cwd=dest)
    else:
        _run(["git", "checkout", snap.target_branch], cwd=dest)
    return str(dest)


def _reset_cached_checkout(dest: Path, snap: "RepoSnapshot") -> None:
    """Return a cached eval checkout to the pinned snapshot (discard prior run edits)."""
    if snap.commit_sha:
        _run(["git", "fetch", "--depth", "1", "origin", snap.commit_sha], cwd=dest)
        _run(["git", "reset", "--hard", snap.commit_sha], cwd=dest)
    elif snap.target_branch:
        _run(["git", "fetch", "origin", snap.target_branch], cwd=dest)
        _run(["git", "reset", "--hard", f"origin/{snap.target_branch}"], cwd=dest)
    _run(["git", "clean", "-fd"], cwd=dest)


def resolve_repo_path(case: "EvalCase") -> str:
    """Primary repo checkout (first entry in case.repos, else case.repo)."""
    repos = case.all_repos()
    if not repos:
        raise ValueError(f"{case.case_id}: no repo configured")
    return resolve_snapshot_path(case.case_id, repos[0])


def resolve_repo_paths(case: "EvalCase") -> list[str]:
    """All repo checkouts for an integration case."""
    repos = case.all_repos()
    if not repos:
        raise ValueError(f"{case.case_id}: no repo configured")
    return [resolve_snapshot_path(case.case_id, snap) for snap in repos]
