"""Jira data schemas — adapted from mentor SCA Fixbot jira_schema.py."""

from __future__ import annotations

import re
from typing import Any, Optional

from pydantic import BaseModel, Field, model_validator

from src.core.github_links import github_slugs_from_text


class JiraTicketRaw(BaseModel):
    """Minimal ticket payload from Jira REST API (mentor jira_client shape)."""

    key: str
    summary: str = ""
    description: str = ""


class JiraEnrichment(BaseModel):
    """Structured security/CVE fields extracted from ticket text."""

    cve_ids: list[str] = Field(default_factory=list)
    severity: Optional[str] = None
    vulnerability_type: Optional[str] = None
    affected_component: Optional[str] = None
    repository: Optional[str] = None
    description_summary: str = ""

    @model_validator(mode="after")
    def _coerce_nulls(self) -> JiraEnrichment:
        if self.cve_ids is None:
            self.cve_ids = []
        return self


_CVE_PATTERN = re.compile(r"CVE-\d{4}-\d{4,}", re.I)
_SEVERITY_PATTERN = re.compile(r"\b(critical|high|medium|low)\b", re.I)
_VULN_TYPES = (
    "sql injection",
    "xss",
    "cross-site scripting",
    "authentication bypass",
    "path traversal",
    "ssrf",
    "rce",
    "remote code execution",
    "idor",
    "csrf",
)


def enrich_security_ticket(raw: JiraTicketRaw) -> JiraEnrichment:
    """Deterministic enrichment from ticket summary + description."""
    text = f"{raw.summary}\n{raw.description}"
    cves = list(dict.fromkeys(m.upper() for m in _CVE_PATTERN.findall(text)))

    severity = None
    sev_match = _SEVERITY_PATTERN.search(text)
    if sev_match:
        severity = sev_match.group(1).lower()
    elif cves:
        severity = "high"

    vuln_type = None
    lower = text.lower()
    for candidate in _VULN_TYPES:
        if candidate in lower:
            vuln_type = candidate
            break

    component = None
    for match in re.findall(r"\b[A-Z][a-zA-Z0-9]{2,}(?:Service|Controller|Handler|Filter|Servlet)\b", text):
        component = match
        break

    slugs = github_slugs_from_text(text)
    repo = slugs[0] if slugs else None

    summary = raw.summary.strip() or raw.description[:300].strip()
    return JiraEnrichment(
        cve_ids=cves,
        severity=severity,
        vulnerability_type=vuln_type,
        affected_component=component,
        repository=repo,
        description_summary=summary,
    )


def raw_to_legacy_fields(raw: JiraTicketRaw, enrichment: JiraEnrichment) -> dict[str, Any]:
    """Map raw + enrichment into legacy JiraTicket TypedDict-compatible fields."""
    return {
        "issue_id": raw.key,
        "title": raw.summary or raw.key,
        "problem_statement": raw.description or raw.summary,
        "enrichment": enrichment.model_dump(),
    }
