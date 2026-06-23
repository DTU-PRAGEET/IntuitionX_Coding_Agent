"""Git repository snapshot helpers — tracing and consistency checks."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from typing import Any

from src.core.logging import get_logger

_log = get_logger("repo_snapshot")


def sha256_text(text: str | None) -> str:
    if text is None:
        return hashlib.sha256(b"").hexdigest()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _run_git(args: list[str], cwd: str, *, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=check,
    )


def git_repo_snapshot(repo_path: str) -> dict[str, Any]:
    """Collect git metadata for a checkout (empty strings when not a git repo)."""
    root = str(Path(repo_path).resolve())
    snap: dict[str, Any] = {
        "repo_path": root,
        "cwd": os.getcwd(),
        "head": "",
        "branch": "",
        "status_short": "",
        "diff_stat": "",
        "is_git": False,
    }
    if not Path(root).exists():
        return snap

    head = _run_git(["rev-parse", "HEAD"], root, check=False)
    branch = _run_git(["branch", "--show-current"], root, check=False)
    status = _run_git(["status", "--short"], root, check=False)
    diff_stat = _run_git(["diff", "--stat"], root, check=False)
    is_git = head.returncode == 0
    snap.update(
        {
            "head": (head.stdout or "").strip() if is_git else "",
            "branch": (branch.stdout or "").strip() if branch.returncode == 0 else "",
            "status_short": (status.stdout or "").strip(),
            "diff_stat": (diff_stat.stdout or "").strip(),
            "is_git": is_git,
        }
    )
    return snap


def log_repo_snapshot(logger: Any, event: str, repo_path: str, **extra: Any) -> dict[str, Any]:
    snap = git_repo_snapshot(repo_path)
    logger.info(event, **snap, **extra)
    return snap


def log_edit_patch(
    logger: Any,
    *,
    phase: str,
    step_id: int,
    file_path: str,
    old_string: str | None,
    new_string: str | None,
    **extra: Any,
) -> None:
    old_s = old_string or ""
    new_s = new_string or ""
    logger.info(
        f"{phase}_edit_patch",
        step_id=step_id,
        file=file_path,
        old_string_len=len(old_s),
        new_string_len=len(new_s),
        old_sha256=sha256_text(old_s),
        new_sha256=sha256_text(new_s),
        old_eq_new=old_s == new_s,
        **extra,
    )


def file_content_hash(repo_path: str, rel_path: str) -> str:
    full = Path(repo_path) / rel_path
    if not full.is_file():
        return ""
    try:
        return sha256_text(full.read_text(encoding="utf-8"))
    except OSError:
        return ""


def old_string_in_file(repo_path: str, rel_path: str, old_string: str) -> dict[str, Any]:
    full = Path(repo_path) / rel_path
    if not full.is_file():
        return {"exists": False, "occurrence_count": 0, "file_sha256": ""}
    try:
        content = full.read_text(encoding="utf-8")
    except OSError:
        return {"exists": False, "occurrence_count": 0, "file_sha256": ""}
    count = content.count(old_string) if old_string else 0
    return {
        "exists": count > 0,
        "occurrence_count": count,
        "file_sha256": sha256_text(content),
    }


def file_git_diff(repo_path: str, rel_path: str) -> str:
    result = _run_git(["diff", "--", rel_path], repo_path, check=False)
    return (result.stdout or result.stderr or "").strip()


def snapshots_match(
    planner_head: str | None,
    executor_head: str | None,
) -> bool:
    if not planner_head:
        return True
    if not executor_head:
        return False
    return planner_head == executor_head
