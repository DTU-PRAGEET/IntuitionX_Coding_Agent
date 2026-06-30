"""Shared inline Jira ticket fixtures for tests."""

from __future__ import annotations

from src.core.state import JiraTicket


def make_security_ticket(ticket_id: str = "SEC-TEST-1") -> JiraTicket:
    """Security-shaped ticket for skill selection and research tests."""
    description = """## Security Finding
SQL injection vulnerability in ExampleHandler endpoint.

CVE-2024-12345 (High severity)

Affected component: ExampleHandler
Repository: platform/auth-service

The query parameter is concatenated directly into SQL without parameterization.

Acceptance criteria:
- Parameterize all SQL queries in ExampleHandler
- Add input validation for search query length
"""
    return JiraTicket(
        issue_id=ticket_id,
        title="SQL injection in ExampleHandler",
        issue_type="Security",
        status="In Progress",
        priority_raw="High",
        priority_normalized="P1",
        module="auth",
        problem_statement=description,
        acceptance_criteria=[
            "Parameterize all SQL queries in ExampleHandler",
            "Add input validation for search query length",
        ],
        assignee="security.team",
        reporter="pen.test",
        labels=["security", "sql-injection", "cve"],
        repo_links=["platform/auth-service"],
        dependencies=[],
        attachments=[],
        comments=[],
        ticket_history_summary={
            "security_enrichment": {
                "cve_ids": ["CVE-2024-12345"],
                "vulnerability_type": "sql injection",
                "severity": "high",
                "affected_component": "ExampleHandler",
            }
        },
        flags=[],
        vulnerability_category="Injection",
    )


def make_bug_ticket(ticket_id: str = "PLAT-9999") -> JiraTicket:
    """Generic bug ticket with no security skill match."""
    return JiraTicket(
        issue_id=ticket_id,
        title=f"Bug ticket {ticket_id}",
        issue_type="Bug",
        status="In Progress",
        priority_raw="Medium",
        priority_normalized="P2",
        module="payments",
        problem_statement="NullPointer in PaymentService.java:42",
        acceptance_criteria=["Fix should not break existing flows"],
        assignee="dev.user",
        reporter="qa.user",
        labels=["bug"],
        repo_links=[],
        dependencies=[],
        attachments=[],
        comments=[],
        ticket_history_summary={},
        flags=[],
        vulnerability_category="",
    )
