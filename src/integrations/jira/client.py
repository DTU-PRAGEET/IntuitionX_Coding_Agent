"""Jira client — fetch by ticket ID and return typed pipeline fields."""

from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from src.agents.orchestrator.tools.jira import (
    _field_name,
    _field_text,
    _issue_field,
    _issue_key,
    _issue_to_jira_ticket,
)
from src.agents.orchestrator.tools.jira_client import (
    create_jira_client,
    credentials_configured,
)
from src.core.logging import get_logger
from src.core.state import JiraTicket

_log = get_logger("jira_client")

JIRA_FETCH_FIELDS = (
    "summary,description,status,issuetype,priority,labels,components,"
    "comment,attachment,issuelinks,assignee,reporter"
)


class ParsedJiraTicket(BaseModel):
    """Pipeline-facing ticket fields extracted from a Jira issue."""

    issue_id: str
    title: str
    issue_type: str
    status: str
    problem_statement: str
    repo_links: list[str] = Field(default_factory=list)
    labels: list[str] = Field(default_factory=list)
    module: str = "not_specified"
    raw_summary: str = ""
    raw_description: str = ""


def _sanitize_raw_payload(issue: Any) -> dict[str, Any]:
    description = _field_text(_issue_field(issue, "description"))
    summary = _field_text(_issue_field(issue, "summary"))
    return {
        "key": _issue_key(issue),
        "summary": summary,
        "description_preview": description[:500],
        "description_length": len(description),
        "description_truncated": len(description) > 500,
        "status": _field_name(_issue_field(issue, "status"), ""),
        "issuetype": _field_name(_issue_field(issue, "issuetype"), ""),
        "priority": _field_name(_issue_field(issue, "priority"), ""),
        "labels": list(_issue_field(issue, "labels") or []),
        "components": [
            c.get("name") if isinstance(c, dict) else getattr(c, "name", str(c))
            for c in (_issue_field(issue, "components") or [])
        ],
    }


def fetch_ticket_by_id(ticket_id: str) -> tuple[JiraTicket, dict[str, Any], ParsedJiraTicket]:
    """Fetch Jira issue by key; return legacy JiraTicket, raw payload log dict, and parsed fields."""
    if not credentials_configured():
        raise ValueError(
            "Jira credentials missing — set JIRA_SERVER_URL, JIRA_EMAIL, JIRA_API_TOKEN"
        )

    try:
        client = create_jira_client()
        issue = client.issue(ticket_id, expand="changelog", fields=JIRA_FETCH_FIELDS)
    except Exception as e:
        raise RuntimeError(
            f"Jira fetch failed for ticket_id={ticket_id!r}: {e}"
        ) from e

    raw_payload = _sanitize_raw_payload(issue)
    _log.info("jira_raw_payload", ticket_id=ticket_id, payload=json.dumps(raw_payload, default=str))

    ticket = _issue_to_jira_ticket(issue, client)

    parsed = ParsedJiraTicket(
        issue_id=ticket.get("issue_id") or ticket_id,
        title=ticket.get("title", "") or issue.key,
        issue_type=ticket.get("issue_type", "Unknown"),
        status=ticket.get("status", "Unknown"),
        problem_statement=ticket.get("problem_statement", "") or ticket.get("title", ""),
        repo_links=list(ticket.get("repo_links", [])),
        labels=list(ticket.get("labels", [])),
        module=ticket.get("module", "not_specified"),
        raw_summary=ticket.get("title", "") or "",
        raw_description=ticket.get("problem_statement") or "",
    )
    _log.info(
        "jira_parsed_fields",
        ticket_id=ticket_id,
        parsed=parsed.model_dump(),
    )
    return ticket, raw_payload, parsed
