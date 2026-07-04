"""Extract GitHub repository links and target branches from Jira ticket text."""

from __future__ import annotations

import re

_GITHUB_URL_RE = re.compile(
    r"https?://github\.com/(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git|/|$|#|\||\s|\])",
    re.IGNORECASE,
)
_BRANCH_RE = re.compile(r"(?i)\bbranch\s*:\s*(\S+)")


def github_slug(owner: str, repo: str) -> str:
    """Github Slug for this module."""
    return f"{owner}/{repo}"


def github_slugs_from_text(text: str) -> list[str]:
    """Return deduplicated owner/repo slugs from GitHub URLs in free text."""
    if not text:
        return []
    slugs: list[str] = []
    for match in _GITHUB_URL_RE.finditer(text):
        slugs.append(github_slug(match.group("owner"), match.group("repo")))
    return list(dict.fromkeys(slugs))


def extract_target_branch(text: str) -> str | None:
    """Parse `Branch: master` style lines from Jira descriptions."""
    if not text:
        return None
    match = _BRANCH_RE.search(text)
    if not match:
        return None
    branch = match.group(1).strip().strip("[]()")
    return branch or None


def extract_repo_links_from_text(text: str) -> list[str]:
    """Primary repo-link extractor — GitHub slugs only, no path fragments."""
    return github_slugs_from_text(text)
