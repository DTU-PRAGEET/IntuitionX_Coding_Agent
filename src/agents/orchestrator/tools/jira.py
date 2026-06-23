"""Jira Context Extractor — Tier 1 + Tier 2."""

from __future__ import annotations

import re
from typing import Any

from src.agents.orchestrator.tools.jira_client import (
    create_jira_client,
    credentials_configured,
    fetch_ticket_by_key,
)
from src.agents.orchestrator.tools.jira_schema import JiraTicketRaw, enrich_security_ticket
from src.core.github_links import extract_repo_links_from_text, extract_target_branch
from src.core.logging import get_logger
from src.core.state import JiraAttachment, JiraComment, JiraDependency, JiraTicket

_log = get_logger("jira")


def _raw_fields(issue: Any) -> dict[str, Any]:
    raw = getattr(issue, "raw", None)
    if isinstance(raw, dict):
        fields = raw.get("fields")
        if isinstance(fields, dict):
            return fields
    return {}


def _issue_field(issue: Any, name: str, default: Any = None) -> Any:
    """Read a Jira field from raw JSON first; avoid PropertyHolder AttributeError."""
    fields = _raw_fields(issue)
    if name in fields:
        return fields[name]
    try:
        return getattr(issue.fields, name, default)
    except AttributeError:
        return default


def _field_name(field_obj: Any, default: str = "Unknown") -> str:
    if not field_obj:
        return default
    if isinstance(field_obj, dict):
        return field_obj.get("name") or default
    return getattr(field_obj, "name", None) or default


def _user_name(user_obj: Any, default: str = "not_specified") -> str:
    if not user_obj:
        return default
    if isinstance(user_obj, dict):
        return user_obj.get("displayName") or user_obj.get("name") or default
    return getattr(user_obj, "displayName", None) or getattr(user_obj, "name", None) or default


def _issue_key(issue: Any) -> str:
    if isinstance(issue, dict):
        return issue.get("key") or issue.get("issue_id") or "UNKNOWN"
    return getattr(issue, "key", None) or "UNKNOWN"


def _link_related_issue(link: Any) -> Any | None:
    if isinstance(link, dict):
        return link.get("outwardIssue") or link.get("inwardIssue")
    return getattr(link, "outwardIssue", None) or getattr(link, "inwardIssue", None)


def _link_relationship(link: Any) -> str:
    if isinstance(link, dict):
        link_type = link.get("type") or {}
        if isinstance(link_type, dict):
            return link_type.get("outward") or link_type.get("inward") or ""
        return ""
    link_type = getattr(link, "type", None)
    if link_type is None:
        return ""
    return getattr(link_type, "outward", "") or getattr(link_type, "inward", "") or ""


def _field_text(field_obj: Any, default: str = "") -> str:
    if not field_obj:
        return default
    if isinstance(field_obj, str):
        return field_obj
    return str(field_obj)


AC_KEYWORDS = [
    "acceptance criteria",
    "story acceptance criteria",
    "desired workflow",
    "qa scenarios",
    "test plan",
    "given",
    "when",
    "then",
    "checklist",
]

PRIORITY_MAP = {
    "critical": "P0",
    "highest": "P0",
    "high": "P1",
    "major": "P1",
    "medium": "P2",
    "minor": "P3",
    "low": "P4",
    "trivial": "P4",
}

DONE_STATUSES = {"done", "resolved", "closed"}


def _normalize_priority(raw: str | None) -> str:
    if not raw:
        return "P3"
    return PRIORITY_MAP.get(raw.lower(), "P3")


def _extract_acceptance_criteria(text: str) -> list[str]:
    if not text:
        return []
    criteria: list[str] = []
    lines = text.splitlines()
    in_ac = False
    for line in lines:
        lower = line.lower().strip()
        if any(kw in lower for kw in AC_KEYWORDS):
            in_ac = True
            continue
        if in_ac:
            stripped = line.strip()
            if not stripped:
                if criteria:
                    break
                continue
            if stripped.startswith(("-", "*", "•")) or re.match(r"^\d+\.", stripped):
                criteria.append(re.sub(r"^[-*•\d.]+\s*", "", stripped))
            elif stripped.startswith(("Given", "When", "Then")):
                criteria.append(stripped)
    # Fallback: bullet items in description
    if not criteria:
        for line in lines:
            stripped = line.strip()
            if stripped.startswith(("-", "*", "•")):
                criteria.append(re.sub(r"^[-*•]+\s*", "", stripped))
    return criteria[:20]


def _compress_comments(comments: list[Any], assignee: str, reporter: str) -> list[JiraComment]:
    keyword_pattern = re.compile(
        r"root cause|rca|blocker|workaround|dependency|fix", re.I
    )
    kept: dict[str, JiraComment] = {}

    def add(c: Any) -> None:
        author = getattr(c.author, "displayName", "unknown")
        body = getattr(c, "body", "") or ""
        created = str(getattr(c, "created", ""))
        key = f"{author}:{created}"
        kept[key] = JiraComment(author=author, body=body, created=created)

    # Most recent 10
    sorted_comments = sorted(comments, key=lambda c: str(getattr(c, "created", "")), reverse=True)
    for c in sorted_comments[:10]:
        add(c)
    # Assignee and reporter
    for c in comments:
        author = getattr(c.author, "displayName", "")
        if author in (assignee, reporter):
            add(c)
    # Keyword comments
    for c in comments:
        body = getattr(c, "body", "") or ""
        if keyword_pattern.search(body):
            add(c)

    result = list(kept.values())
    if len(comments) > 15:
        result.append(
            JiraComment(
                author="system",
                body="Comment history compressed.",
                created="",
            )
        )
    return result[:30]


def _extract_repo_links(issue: Any) -> list[str]:
    description = _field_text(_issue_field(issue, "description"))
    links = extract_repo_links_from_text(description)
    for _field_name, val in _raw_fields(issue).items():
        if isinstance(val, str) and "github.com" in val.lower():
            links.extend(extract_repo_links_from_text(val))
    return list(dict.fromkeys(links))[:5]


def _issue_to_jira_ticket(issue: Any, client: Any) -> JiraTicket:
    """Convert a Jira issue object into a normalized JiraTicket."""
    summary = _field_text(_issue_field(issue, "summary"))
    description = _field_text(_issue_field(issue, "description"))
    raw = JiraTicketRaw(key=_issue_key(issue), summary=summary, description=description)
    enrichment = enrich_security_ticket(raw)

    status = _field_name(_issue_field(issue, "status"), "Unknown")
    assignee = _user_name(_issue_field(issue, "assignee"))
    reporter = _user_name(_issue_field(issue, "reporter"))
    priority_raw = _field_name(_issue_field(issue, "priority"), "Medium")
    labels = list(_issue_field(issue, "labels") or [])
    components_raw = _issue_field(issue, "components") or []
    components = [
        c.get("name") if isinstance(c, dict) else getattr(c, "name", str(c))
        for c in components_raw
    ]
    module = components[0] if components else "not_specified"

    comments_raw = client.comments(issue)
    comments = _compress_comments(comments_raw, assignee, reporter)

    acceptance = _extract_acceptance_criteria(description)
    if not acceptance:
        for c in comments:
            acceptance.extend(_extract_acceptance_criteria(c["body"]))
        acceptance = list(dict.fromkeys(acceptance))

    dependencies: list[JiraDependency] = []
    for link in _issue_field(issue, "issuelinks") or []:
        linked = _link_related_issue(link)
        if not linked:
            continue
        rel = _link_relationship(link)
        linked_status = _field_name(_issue_field(linked, "status"), "unknown")
        linked_summary = _field_text(_issue_field(linked, "summary"), _issue_key(linked))
        dependencies.append(
            JiraDependency(
                issue_id=_issue_key(linked),
                relationship=rel,
                status=linked_status,
                summary=linked_summary,
            )
        )

    attachments: list[JiraAttachment] = []
    for att in _issue_field(issue, "attachment") or []:
        filename = att.get("filename") if isinstance(att, dict) else getattr(att, "filename", "unknown")
        mime = att.get("mimeType") if isinstance(att, dict) else getattr(att, "mimeType", "unknown")
        attachments.append(JiraAttachment(name=filename, type=mime or "unknown"))

    flags: list[str] = []
    if status.lower() in DONE_STATUSES:
        flags.append("Ticket is already marked as Done.")
    if not acceptance:
        flags.append("Acceptance criteria missing. Human review recommended.")
        acceptance_str: list[str] | str = "not_specified"
    else:
        acceptance_str = acceptance

    if not description.strip():
        flags.append("Insufficient description. Cannot extract meaningful requirements.")

    repo_links = _extract_repo_links(issue)
    if enrichment.repository and enrichment.repository not in repo_links:
        repo_links.insert(0, enrichment.repository)

    target_branch = extract_target_branch(description)

    issue_type = _field_name(_issue_field(issue, "issuetype"), "Unknown")

    return JiraTicket(
        issue_id=_issue_key(issue),
        title=summary or _issue_key(issue),
        issue_type=issue_type,
        status=status,
        priority_raw=priority_raw,
        priority_normalized=_normalize_priority(priority_raw),  # type: ignore
        module=module,
        problem_statement=description or summary,
        acceptance_criteria=acceptance_str,
        assignee=assignee,
        reporter=reporter,
        labels=labels,
        repo_links=repo_links,
        dependencies=dependencies,
        attachments=attachments,
        comments=comments,
        ticket_history_summary={
            "fetched": True,
            "security_enrichment": enrichment.model_dump(),
            "github_repo": repo_links[0] if repo_links else enrichment.repository,
            "target_branch": target_branch,
        },
        flags=flags,
    )


def fetch_jira_ticket(ticket_id: str) -> JiraTicket:
    """Fetch and normalize Jira issue — Tier 1 + Tier 2 + security enrichment."""
    if not credentials_configured():
        _log.info("using_mock", ticket_id=ticket_id, reason="credentials_not_configured")
        return _mock_jira_ticket(ticket_id)

    try:
        client = create_jira_client()
        issue = client.issue(ticket_id, expand="changelog")
        return _issue_to_jira_ticket(issue, client)
    except Exception as e:
        _log.error("fetch_failed", ticket_id=ticket_id, error=str(e))
        ticket = _mock_jira_ticket(ticket_id)
        ticket["flags"] = list(ticket.get("flags", [])) + [f"Jira fetch failed: {e}"]
        return ticket


def mock_security_jira_ticket(ticket_id: str = "SEC-4421") -> JiraTicket:
    """Sample security ticket for standalone Research Agent verification."""
    description = """## Security Finding
SQL injection vulnerability in UserSearchController.searchUsers endpoint.

CVE-2024-12345 (High severity)

Affected component: UserSearchController
Demo file in this repo: src/agents/planner/fixtures/demo/UserSearchController.py
Repository: platform/auth-service

The `query` parameter is concatenated directly into SQL without parameterization.
Pen test report: attacker can extract user records via `' OR 1=1 --`.

Acceptance criteria:
- Parameterize all SQL queries in UserSearchController
- Add input validation for search query length
- No regression in existing search API tests
"""
    raw_enrichment = enrich_security_ticket(
        JiraTicketRaw(key=ticket_id, summary="SQL injection in UserSearchController", description=description)
    )
    return JiraTicket(
        issue_id=ticket_id,
        title="SQL injection in UserSearchController.searchUsers",
        issue_type="Security",
        status="In Progress",
        priority_raw="High",
        priority_normalized="P1",
        module="auth",
        problem_statement=description,
        acceptance_criteria=[
            "Parameterize all SQL queries in UserSearchController",
            "Add input validation for search query length",
        ],
        assignee="security.team",
        reporter="pen.test",
        labels=["security", "sql-injection", "cve"],
        repo_links=["platform/auth-service"],
        dependencies=[],
        attachments=[],
        comments=[],
        ticket_history_summary={"mock": True, "security_enrichment": raw_enrichment.model_dump()},
        flags=["Mock security ticket for local Research Agent runs."],
    )


def _mock_jira_ticket(ticket_id: str) -> JiraTicket:
    """Fallback when Jira credentials are not configured (local dev)."""
    return JiraTicket(
        issue_id=ticket_id,
        title=f"Mock ticket {ticket_id}",
        issue_type="Bug",
        status="In Progress",
        priority_raw="Medium",
        priority_normalized="P2",
        module="payments",
        problem_statement="Mock problem statement for local development.",
        acceptance_criteria=["Fix should not break existing flows"],
        assignee="dev.user",
        reporter="qa.user",
        labels=["mock"],
        repo_links=[],
        dependencies=[],
        attachments=[],
        comments=[],
        ticket_history_summary={},
        flags=["Jira credentials not configured — using mock data."],
    )


def is_ticket_done(ticket: JiraTicket) -> bool:
    return ticket["status"].lower() in DONE_STATUSES
