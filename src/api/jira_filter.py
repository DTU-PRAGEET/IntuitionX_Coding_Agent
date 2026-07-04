"""Jira webhook filtering — gate enqueue before pipeline work."""

from __future__ import annotations

from typing import Any

from src.core.config import settings


def _issue_labels(payload: dict[str, Any]) -> set[str]:
    """Issue Labels.
    Supports the surrounding module workflow.
    """
    issue = payload.get("issue") or {}
    fields = issue.get("fields") or {}
    labels = fields.get("labels") or []
    return {str(label).lower() for label in labels}


def _status_transition_to(payload: dict[str, Any]) -> set[str]:
    """Status Transition To.
    Supports the surrounding module workflow.
    """
    changelog = payload.get("changelog") or {}
    targets: set[str] = set()
    for item in changelog.get("items") or []:
        if item.get("field") == "status" and item.get("toString"):
            targets.add(str(item["toString"]))
    return targets


def passes_webhook_filter(payload: dict[str, Any]) -> tuple[bool, str]:
    """Return (allowed, reason). Filter runs before enqueue."""
    required_labels = settings.webhook_required_labels
    allowed_transitions = settings.webhook_allowed_transitions
    webhook_event = payload.get("webhookEvent")

    labels = _issue_labels(payload)
    has_required_label = not required_labels or bool(labels.intersection(required_labels))

    if webhook_event == "jira:issue_created":
        if has_required_label:
            return True, "label_match"
        label_hint = ", ".join(sorted(required_labels)) or "(none configured)"
        return False, f"issue_created requires label(s): {label_hint}"

    if webhook_event == "jira:issue_updated":
        if has_required_label:
            return True, "label_match"
        transitions = _status_transition_to(payload)
        matched = transitions.intersection(allowed_transitions)
        if matched:
            return True, f"status_transition:{next(iter(matched))}"
        transition_hint = ", ".join(sorted(allowed_transitions)) or "(none configured)"
        label_hint = ", ".join(sorted(required_labels)) or "(none configured)"
        return (
            False,
            "issue_updated requires label(s) "
            f"{label_hint} or transition to {transition_hint}",
        )

    # Direct test payloads without webhookEvent still require labels when configured.
    if payload.get("ticket_id") and not webhook_event:
        if has_required_label or not required_labels:
            return True, "direct_ticket_id"
        return False, f"direct payload requires label(s): {', '.join(sorted(required_labels))}"

    return False, "unsupported or missing webhookEvent"
