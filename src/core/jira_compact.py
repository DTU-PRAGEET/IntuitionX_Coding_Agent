# """Token-minimal Jira views for LLM prompts — full ticket stays in AgentState only."""

# from __future__ import annotations

# from typing import Any

# from src.core.state import JiraDependency, JiraTicket

# _PROBLEM_MAX = {"orchestrator": 1200, "research": 2000, "planner": 400}
# _ACCEPTANCE_ITEM_MAX = 200
# _ACCEPTANCE_MAX_ITEMS = 5
# _COMMENT_BODY_MAX = 150
# _COMMENT_MAX = 2
# _DEPENDENCY_MAX = 3


# def _truncate(text: str, limit: int) -> str:
#     text = (text or "").strip()
#     if len(text) <= limit:
#         return text
#     return text[: limit - 3].rstrip() + "..."


# def _compact_acceptance(ac: list[str] | str, *, max_items: int = _ACCEPTANCE_MAX_ITEMS) -> list[str]:
#     if isinstance(ac, str):
#         if not ac or ac == "not_specified":
#             return []
#         return [_truncate(ac, _ACCEPTANCE_ITEM_MAX)]
#     return [_truncate(item, _ACCEPTANCE_ITEM_MAX) for item in ac[:max_items] if item]


# def _compact_dependencies(deps: list[JiraDependency], *, max_items: int = _DEPENDENCY_MAX) -> list[dict[str, str]]:
#     return [
#         {
#             "issue_id": d.get("issue_id", ""),
#             "relationship": d.get("relationship", ""),
#             "summary": _truncate(d.get("summary", ""), 120),
#         }
#         for d in deps[:max_items]
#     ]


# def _compact_comments(ticket: JiraTicket) -> list[dict[str, str]]:
#     comments = ticket.get("comments") or []
#     out: list[dict[str, str]] = []
#     for c in comments[-_COMMENT_MAX:]:
#         out.append(
#             {
#                 "author": (c.get("author") or "")[:40],
#                 "body": _truncate(c.get("body") or "", _COMMENT_BODY_MAX),
#             }
#         )
#     return out


# def compact_jira_for_agent(ticket: JiraTicket | None, agent: str) -> dict[str, Any] | None:
#     """Return a minimal ticket dict for the given agent phase."""
#     if not ticket:
#         return None

#     problem_limit = _PROBLEM_MAX.get(agent, 1200)
#     base: dict[str, Any] = {
#         "issue_id": ticket.get("issue_id", ""),
#         "title": _truncate(ticket.get("title") or "", 200),
#         "issue_type": ticket.get("issue_type", ""),
#         "status": ticket.get("status", ""),
#         "priority": ticket.get("priority_normalized") or ticket.get("priority_raw", ""),
#         "labels": (ticket.get("labels") or [])[:12],
#         "module": ticket.get("module", ""),
#         "repo_links": (ticket.get("repo_links") or [])[:3],
#         "flags": (ticket.get("flags") or [])[:5],
#     }

#     if agent == "planner":
#         base["acceptance_criteria"] = _compact_acceptance(ticket.get("acceptance_criteria") or [])
#         return base

#     base["problem_statement"] = _truncate(ticket.get("problem_statement") or "", problem_limit)
#     base["acceptance_criteria"] = _compact_acceptance(ticket.get("acceptance_criteria") or [])

#     if agent == "orchestrator":
#         base["dependencies"] = _compact_dependencies(ticket.get("dependencies") or [])
#         return base

#     if agent == "research":
#         base["recent_comments"] = _compact_comments(ticket)
#         return base

#     return base
