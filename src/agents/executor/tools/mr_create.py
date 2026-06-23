"""Create GitHub Pull Requests from executor worktrees."""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.request
from typing import Any

from src.core.config import settings
from src.core.logging import get_logger
from src.core.github_links import extract_repo_links_from_text, github_slugs_from_text
from src.core.state import JiraTicket

_log = get_logger("mr_create")

_GITHUB_REPO_RE = re.compile(
    r"github\.com[:/](?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git|/|$|#|\|)"
)


def _run_git(args: list[str], cwd: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        check=False,
    )


def parse_github_owner_repo(text: str) -> str | None:
    """Extract owner/repo from a GitHub URL or slug embedded in ticket text."""
    slugs = github_slugs_from_text(text or "")
    if slugs:
        return slugs[0]
    match = _GITHUB_REPO_RE.search(text or "")
    if match:
        return f"{match.group('owner')}/{match.group('repo')}"
    return None


def github_repo_from_ticket(ticket: JiraTicket | None) -> str | None:
    """Best-effort GitHub slug from Jira ticket links or description."""
    if not ticket:
        return None
    for link in ticket.get("repo_links") or []:
        parsed = parse_github_owner_repo(link)
        if parsed:
            return parsed
    slugs = extract_repo_links_from_text(ticket.get("problem_statement") or "")
    return slugs[0] if slugs else None


def resolve_github_repo(
    repo_path: str,
    *,
    github_repo: str | None = None,
    ticket: JiraTicket | None = None,
) -> str:
    """Resolve owner/repo from explicit override, env, git remote, or ticket."""
    if github_repo:
        return github_repo.strip().strip("/")
    if settings.github_repo:
        return settings.github_repo.strip().strip("/")

    result = _run_git(["remote", "get-url", "origin"], repo_path)
    if result.returncode == 0:
        url = (result.stdout or "").strip()
        parsed = parse_github_owner_repo(url)
        if parsed:
            return parsed

    ticket_repo = github_repo_from_ticket(ticket)
    if ticket_repo:
        return ticket_repo

    if result.returncode != 0:
        raise RuntimeError(
            f"Cannot resolve GitHub repo for repo_path={repo_path!r}: "
            f"set GITHUB_REPO or configure origin remote ({result.stderr.strip()})"
        )
    raise RuntimeError(
        f"Origin remote is not a GitHub URL ({url!r}); set GITHUB_REPO=owner/repo"
    )


def _github_request(method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
    token = settings.github_token
    if not token:
        raise RuntimeError(
            "GitHub token missing — set GITHUB_TOKEN in .env.local before creating PRs"
        )
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "sprinklr-jira-solver",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8")
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise RuntimeError(
            f"GitHub API {method} {path} failed ({e.code}): {detail[:1000]}"
        ) from e
    except urllib.error.URLError as e:
        raise RuntimeError(f"GitHub API request failed for {path}: {e}") from e


def _remote_branch_exists(repo_path: str, branch: str) -> bool:
    result = _run_git(["ls-remote", "--heads", "origin", branch], repo_path)
    return bool((result.stdout or "").strip())


def _preflight_github_pr(
    repo_path: str,
    github_repo: str,
    branch_name: str,
    base: str,
) -> None:
    """Validate repo access and branch refs before opening a PR."""
    try:
        _github_request("GET", f"/repos/{github_repo}")
    except RuntimeError as e:
        if "404" in str(e):
            raise RuntimeError(
                f"GitHub repo {github_repo!r} is not accessible with GITHUB_TOKEN. "
                "For private repos, use a classic PAT with the 'repo' scope or a "
                "fine-grained PAT with Contents (read/write) and Pull requests "
                "(read/write) for this repository. Also verify GITHUB_REPO and "
                f"git remote origin match the repo you pushed to. API: {e}"
            ) from e
        raise

    if not _remote_branch_exists(repo_path, branch_name):
        raise RuntimeError(
            f"Head branch {branch_name!r} is not on origin — push the branch before "
            f"creating a PR. Run `git push -u origin {branch_name}` from the worktree."
        )

    try:
        _github_request("GET", f"/repos/{github_repo}/branches/{base}")
    except RuntimeError as e:
        if "404" in str(e):
            remote_branches = _run_git(["ls-remote", "--heads", "origin"], repo_path)
            available = [
                line.rsplit("/", 1)[-1]
                for line in (remote_branches.stdout or "").splitlines()
                if line.strip()
            ]
            raise RuntimeError(
                f"Base branch {base!r} not found on {github_repo}. "
                f"Remote branches: {', '.join(available) or 'none'}. "
                f"Set GIT_TARGET_BRANCH to an existing branch (e.g. main). API: {e}"
            ) from e
        raise


def create_github_pr(
    repo_path: str,
    branch_name: str,
    *,
    ticket_id: str,
    title: str,
    body: str,
    base_branch: str | None = None,
    github_repo: str | None = None,
    ticket: JiraTicket | None = None,
    require_remote_head: bool = True,
) -> str:
    """Push branch and open a GitHub Pull Request; return the PR HTML URL."""
    base = base_branch or settings.git_target_branch
    resolved_repo = resolve_github_repo(repo_path, github_repo=github_repo, ticket=ticket)

    if require_remote_head:
        _preflight_github_pr(repo_path, resolved_repo, branch_name, base)

    pr_title = title or f"[AUTO] Fix for {ticket_id}"
    pr_body = body or f"Automated fix for Jira ticket {ticket_id}."
    _log.info("create_pr", repo=resolved_repo, head=branch_name, base=base)
    try:
        result = _github_request(
            "POST",
            f"/repos/{resolved_repo}/pulls",
            {
                "title": pr_title,
                "head": branch_name,
                "base": base,
                "body": pr_body,
            },
        )
    except RuntimeError as e:
        if "404" in str(e):
            raise RuntimeError(
                f"GitHub PR creation failed for ticket={ticket_id!r} "
                f"repo={resolved_repo!r} branch={branch_name!r} base={base!r}: {e}. "
                "GitHub often returns 404 when the token cannot access the repo, "
                "the head/base branch is missing on the remote, or owner/repo is wrong."
            ) from e
        raise RuntimeError(
            f"GitHub PR creation failed for ticket={ticket_id!r} "
            f"repo={resolved_repo!r} branch={branch_name!r}: {e}"
        ) from e

    html_url = result.get("html_url", "")
    if not html_url:
        raise RuntimeError(
            f"GitHub PR response missing html_url for ticket={ticket_id!r}: {result}"
        )
    _log.info("pr_created", url=html_url, number=result.get("number"))
    return html_url
