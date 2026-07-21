"""Jira client — adapted from mentor jira_client.py (sync wrappers for agent runners)."""

from __future__ import annotations

import re
from typing import Optional

from jira import JIRA

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
    """Credentials Configured for this module."""
    return bool(settings.jira_server_url and settings.jira_email and settings.jira_api_token)



def extract_ticket_id_from_url(url: str) -> Optional[str]:
    """Extract ticket id from url."""
    for pattern in (r"/browse/([A-Z]+-\d+)", r"/([A-Z]+-\d+)"):
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None
