"""Tests for Jira client helpers."""

from __future__ import annotations

import pytest

from src.agents.orchestrator.tools.jira_client import extract_ticket_id_from_url
from src.agents.orchestrator.tools.jira_schema import JiraTicketRaw, enrich_security_ticket


def test_extract_ticket_id_from_url():
    assert extract_ticket_id_from_url("https://foo.atlassian.net/browse/SEC-123") == "SEC-123"
    assert extract_ticket_id_from_url("/browse/PLAT-9") == "PLAT-9"
    assert extract_ticket_id_from_url("no-ticket-here") is None


def test_jira_ticket_raw_model():
    raw = JiraTicketRaw(key="SEC-1", summary="s", description="d")
    assert raw.key == "SEC-1"
