"""Signal-driven file discovery for Research Agent."""

from __future__ import annotations

import re
from pathlib import Path

from src.core.state import JiraTicket, RepoProfile


def extract_anchors(ticket: JiraTicket, user_prompt: str) -> list[str]:
    """Extract search anchors from Jira ticket and user prompt."""
    text = " ".join(
        [
            ticket.get("problem_statement", "") or "",
            ticket.get("title", "") or "",
            user_prompt or "",
            " ".join(c["body"] for c in ticket.get("comments", [])),
        ]
    )
    anchors: list[str] = []

    for match in re.findall(r"[\w/\\.]+\.(?:java|py|ts|js|kt):\d+", text):
        anchors.append(match.split(":")[0])

    for match in re.findall(r"\b[A-Z][a-zA-Z0-9]{2,}(?:Service|Controller|Handler|Manager|Util)?\b", text):
        anchors.append(match)

    for match in re.findall(r"[\w/\\.-]+\.(?:java|py|yml|yaml|xml|gradle|properties)", text):
        anchors.append(match)

    for match in re.findall(r"(?:Exception|Error)[:\s]+([^\n.]{10,80})", text):
        anchors.append(match.strip())

    return list(dict.fromkeys(a for a in anchors if len(a) > 2))[:20]


def rank_candidate_files(
    repo_path: str,
    anchors: list[str],
    repo_profile: RepoProfile | None,
) -> list[tuple[str, float, str]]:
    """Rank files by signal strength. Returns (path, score, reason)."""
    root = Path(repo_path)
    if not root.exists():
        return []

    candidates: dict[str, tuple[float, str]] = {}

    for anchor in anchors:
        anchor_lower = anchor.lower()
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            if any(
                skip in str(path)
                for skip in (".git", "node_modules", "__pycache__", ".gradle", "/build/")
            ):
                continue
            rel = str(path.relative_to(root))
            rel_lower = rel.lower()
            score = 0.0
            reason = ""

            if anchor_lower in rel_lower or rel.endswith(anchor):
                score += 10.0
                reason = f"path matches anchor '{anchor}'"
            elif path.stem.lower() == anchor_lower.replace(".java", "").replace(".py", ""):
                score += 8.0
                reason = f"filename matches '{anchor}'"
            else:
                try:
                    content = path.read_text(encoding="utf-8", errors="replace")
                    if anchor in content:
                        score += 5.0
                        reason = f"content contains '{anchor}'"
                except OSError:
                    continue

            if score > 0:
                prev = candidates.get(rel, (0.0, ""))
                candidates[rel] = (max(prev[0], score), reason or prev[1])

    ranked = sorted(
        [(p, s, r) for p, (s, r) in candidates.items()],
        key=lambda x: x[1],
        reverse=True,
    )
    return ranked[:15]
