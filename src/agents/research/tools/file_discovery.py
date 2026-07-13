"""Signal-driven file discovery for Research Agent."""

from __future__ import annotations

import re

from src.core.state import JiraTicket

# --- M1.3 commented out: rank_candidate_files helpers (see docs/cleanup-audit.md) ---
# import os
# from pathlib import Path
#
# _SKIP_DIR_NAMES = frozenset(
#     {
#         ".git",
#         ".venv",
#         "venv",
#         "node_modules",
#         "__pycache__",
#         ".gradle",
#         "build",
#         ".cursor",
#         ".pytest_cache",
#         ".mypy_cache",
#         "dist",
#         "egg-info",
#     }
# )
#
#
# def _iter_repo_files(root: Path):
#     """Yield repo files once, pruning heavy directories during walk."""
#     for current_root, dirnames, filenames in os.walk(root):
#         dirnames[:] = [
#             name
#             for name in dirnames
#             if name not in _SKIP_DIR_NAMES and not name.startswith(".")
#         ]
#         base = Path(current_root)
#         for filename in filenames:
#             yield base / filename
# --- end M1.3: rank_candidate_files helpers ---


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


# --- M1.3 commented out: rank_candidate_files (see docs/cleanup-audit.md) ---
# def rank_candidate_files(
#     repo_path: str,
#     anchors: list[str],
#     repo_profile: RepoProfile | None,
# ) -> list[tuple[str, float, str]]:
#     """Rank files by signal strength. Returns (path, score, reason)."""
#     root = Path(repo_path)
#     if not root.exists():
#         return []
#
#     candidates: dict[str, tuple[float, str]] = {}
#     files_scanned = 0
#
#     anchor_lowers = [(a, a.lower()) for a in anchors]
#
#     for path in _iter_repo_files(root):
#         files_scanned += 1
#         if files_scanned > 5000:
#             break
#         rel = str(path.relative_to(root))
#         rel_lower = rel.lower()
#         stem_lower = path.stem.lower()
#
#         for anchor, anchor_lower in anchor_lowers:
#             score = 0.0
#             reason = ""
#
#             if anchor_lower in rel_lower or rel.endswith(anchor):
#                 score += 10.0
#                 reason = f"path matches anchor '{anchor}'"
#             elif stem_lower == anchor_lower.replace(".java", "").replace(".py", ""):
#                 score += 8.0
#                 reason = f"filename matches '{anchor}'"
#             elif len(anchor) >= 4 and anchor_lower in rel_lower:
#                 score += 6.0
#                 reason = f"path contains '{anchor}'"
#             else:
#                 if len(anchor) < 5 or anchor_lower in {"security", "finding", "high", "demo", "repository"}:
#                     continue
#                 try:
#                     content = path.read_text(encoding="utf-8", errors="replace")
#                     if anchor in content:
#                         score += 5.0
#                         reason = f"content contains '{anchor}'"
#                 except OSError:
#                     continue
#
#             if score > 0:
#                 prev = candidates.get(rel, (0.0, ""))
#                 candidates[rel] = (max(prev[0], score), reason or prev[1])
#
#     ranked = sorted(
#         [(p, s, r) for p, (s, r) in candidates.items()],
#         key=lambda x: x[1],
#         reverse=True,
#     )
#     return ranked[:15]
# --- end M1.3: rank_candidate_files ---
