"""Signal-driven file discovery for Research Agent."""

from __future__ import annotations

import re

from src.core.state import JiraTicket

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
