"""Human-readable HITL pause text for API clients (same as Chainlit gate messages)."""

from __future__ import annotations

from typing import Any

from src.api.schemas import TicketJobStatus


def pause_display_message(
    *,
    status: TicketJobStatus,
    pause_context: dict[str, Any] | None,
) -> str | None:
    """Return formatted gate message when job is paused, else None."""
    if status != TicketJobStatus.PAUSED or not pause_context:
        return None
    message = pause_context.get("message")
    if isinstance(message, str) and message.strip():
        return message
    return _fallback_pause_message(pause_context)


def pause_display_with_actions(
    *,
    status: TicketJobStatus,
    pause_context: dict[str, Any] | None,
    thread_id: str,
    job_id: str,
) -> str:
    """Full plain-text block: gate message + how to approve/reject/revise."""
    body = pause_display_message(status=status, pause_context=pause_context)
    if not body:
        body = "Pipeline is paused but no display message is available."

    footer = [
        "",
        "=" * 60,
        f"thread_id: {thread_id}",
        f"job_id:    {job_id}",
        "",
        "To continue, run:",
        f'  curl -X POST "http://localhost:8000/approve/{thread_id}" \\',
        '    -H "Content-Type: application/json" \\',
        '    -d \'{"status":"APPROVE"}\'',
        "",
        "Or REJECT / REVISE with optional feedback:",
        '  -d \'{"status":"REVISE","feedback":"your notes here"}\'',
    ]
    return body + "\n".join(footer)


def _fallback_pause_message(pause_context: dict[str, Any]) -> str:
    """Build minimal readable text when interrupt message is missing."""
    gate = pause_context.get("gate", "GATE")
    lines = [f"--- APPROVAL REQUIRED: {gate} ---", ""]
    research = pause_context.get("research_output")
    if isinstance(research, dict):
        summary = research.get("research_summary") or ""
        if summary:
            lines.extend(["Research summary:", summary, ""])
        hypotheses = research.get("hypotheses") or []
        if hypotheses:
            lines.append("Top hypothesis:")
            lines.append(hypotheses[0].get("hypothesis", ""))
            lines.append("")
    plan = pause_context.get("plan_output")
    if isinstance(plan, dict):
        lines.extend(["Plan:", plan.get("root_hypothesis", ""), ""])
    lines.append("Please respond with: APPROVE / REJECT / REVISE [notes]")
    return "\n".join(lines)
