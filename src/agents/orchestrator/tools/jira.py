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
from src.core.logging import get_logger
from src.core.state import JiraAttachment, JiraComment, JiraDependency, JiraTicket

_log = get_logger("jira")

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
    links: list[str] = []
    description = getattr(issue.fields, "description", "") or ""
    # Common patterns in description
    for match in re.findall(r"[\w-]+/[\w-]+(?:/[\w-]+)?", description):
        if "http" not in match:
            links.append(match)
    # Custom fields — scan all string fields
    for field_name in dir(issue.fields):
        if field_name.startswith("_"):
            continue
        val = getattr(issue.fields, field_name, None)
        if isinstance(val, str) and ("repo" in val.lower() or "git" in val.lower()):
            links.append(val)
    return list(dict.fromkeys(links))[:5]


def fetch_jira_ticket(ticket_id: str) -> JiraTicket:
    """Fetch and normalize Jira issue — Tier 1 + Tier 2 + security enrichment."""
    if not credentials_configured():
        _log.info("using_mock", ticket_id=ticket_id, reason="credentials_not_configured")
        return _mock_jira_ticket(ticket_id)

    try:
        client = create_jira_client()
        raw = fetch_ticket_by_key(client, ticket_id)
        enrichment = enrich_security_ticket(raw)
        issue = client.issue(ticket_id, expand="changelog")
    except Exception as e:
        _log.error("fetch_failed", ticket_id=ticket_id, error=str(e))
        ticket = _mock_jira_ticket(ticket_id)
        ticket["flags"] = list(ticket.get("flags", [])) + [f"Jira fetch failed: {e}"]
        return ticket

    status = issue.fields.status.name
    assignee = (
        issue.fields.assignee.displayName if issue.fields.assignee else "not_specified"
    )
    reporter = (
        issue.fields.reporter.displayName if issue.fields.reporter else "not_specified"
    )
    description = issue.fields.description or ""
    priority_raw = issue.fields.priority.name if issue.fields.priority else "Medium"
    labels = list(issue.fields.labels or [])
    components = [c.name for c in (issue.fields.components or [])]
    module = components[0] if components else "not_specified"

    comments_raw = client.comments(issue)
    comments = _compress_comments(comments_raw, assignee, reporter)

    acceptance = _extract_acceptance_criteria(description)
    if not acceptance:
        for c in comments:
            acceptance.extend(_extract_acceptance_criteria(c["body"]))
        acceptance = list(dict.fromkeys(acceptance))

    dependencies: list[JiraDependency] = []
    for link in issue.fields.issuelinks or []:
        linked = getattr(link, "outwardIssue", None) or getattr(link, "inwardIssue", None)
        if linked:
            rel = getattr(link.type, "outward", "") or getattr(link.type, "inward", "")
            dependencies.append(
                JiraDependency(
                    issue_id=linked.key,
                    relationship=rel,
                    status=linked.fields.status.name,
                    summary=linked.fields.summary,
                )
            )

    attachments: list[JiraAttachment] = []
    for att in issue.fields.attachment or []:
        attachments.append(
            JiraAttachment(name=att.filename, type=getattr(att, "mimeType", "unknown"))
        )

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

    return JiraTicket(
        issue_id=issue.key,
        title=issue.fields.summary,
        issue_type=issue.fields.issuetype.name,
        status=status,
        priority_raw=priority_raw,
        priority_normalized=_normalize_priority(priority_raw),  # type: ignore
        module=module,
        problem_statement=description or issue.fields.summary,
        acceptance_criteria=acceptance_str,
        assignee=assignee,
        reporter=reporter,
        labels=labels,
        repo_links=repo_links,
        dependencies=dependencies,
        attachments=attachments,
        comments=comments,
        ticket_history_summary={"fetched": True, "security_enrichment": enrichment.model_dump()},
        flags=flags,
    )


def mock_security_jira_ticket(ticket_id: str = "SEC-4421") -> JiraTicket:
    """Sample security ticket for standalone Research Agent verification."""
    description = """## Security Finding
SQL injection vulnerability in UserSearchController.searchUsers endpoint.

CVE-2024-12345 (High severity)

Affected component: UserSearchController.java
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
