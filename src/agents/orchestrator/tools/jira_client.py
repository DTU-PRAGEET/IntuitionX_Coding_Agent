"""Jira client — adapted from mentor jira_client.py (sync wrappers for agent runners)."""

from __future__ import annotations

import re
from typing import Optional

from jira import JIRA

from src.agents.orchestrator.tools.jira_schema import JiraTicketRaw
from src.core.config import settings
from src.core.logging import get_logger

_log = get_logger("jira_client")


class JiraCredentialsError(RuntimeError):
    """Raised when Jira API credentials are missing or invalid."""


def create_jira_client(
    atlassian_url: Optional[str] = None,
    username: Optional[str] = None,
    api_token: Optional[str] = None,
) -> JIRA:
    """Create a JIRA client. Raises if credentials are missing."""
    server = atlassian_url or settings.jira_server_url
    email = username or settings.jira_email
    token = api_token or settings.jira_api_token
    if not all([server, email, token]):
        raise ValueError(
            "Jira credentials missing — set JIRA_SERVER_URL, JIRA_EMAIL, JIRA_API_TOKEN"
        )
    try:
        return JIRA(server=server, basic_auth=(email, token))
    except Exception as e:
        raise RuntimeError(f"Failed to create Jira client for server={server!r}: {e}") from e


def credentials_configured() -> bool:
    return bool(settings.jira_server_url and settings.jira_email and settings.jira_api_token)


def fetch_ticket_by_key(client: JIRA, issue_key: str) -> JiraTicketRaw:
    """Fetch a single Jira ticket by key."""

    def _fetch() -> JiraTicketRaw:
        issue = client.issue(issue_key, fields="summary,description,status,issuetype,priority,labels,components,comment")
        return JiraTicketRaw(
            key=issue.key,
            summary=issue.fields.summary or "",
            description=getattr(issue.fields, "description", "") or "",
        )

    try:
        raw = _fetch()
        _log.info("fetched", issue_key=issue_key)
        return raw
    except Exception as e:
        raise RuntimeError(f"Jira fetch failed for issue_key={issue_key!r}: {e}") from e


def fetch_tickets_from_jql(client: JIRA, jql: str, max_results: int = 50) -> list[JiraTicketRaw]:
    """Run JQL and return minimal ticket payloads."""

    def _fetch() -> list[JiraTicketRaw]:
        issues = client.search_issues(jql, maxResults=max_results, fields="summary,description")
        return [
            JiraTicketRaw(
                key=issue.key,
                summary=issue.fields.summary or "",
                description=getattr(issue.fields, "description", "") or "",
            )
            for issue in issues
        ]

    try:
        tickets = _fetch()
        _log.info("jql_fetched", count=len(tickets), jql=jql[:120])
        return tickets
    except Exception as e:
        raise RuntimeError(f"Jira JQL fetch failed for jql={jql!r}: {e}") from e


def extract_ticket_id_from_url(url: str) -> Optional[str]:
    for pattern in (r"/browse/([A-Z]+-\d+)", r"/([A-Z]+-\d+)"):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None
