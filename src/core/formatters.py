"""Human-readable formatters for HITL approval requests."""

from __future__ import annotations

from __future__ import annotations

from src.core.state import AgentState, PlanOutput, ResearchOutput


def format_gate1_request(state: AgentState) -> str:
    research: ResearchOutput | None = state.get("research_output")
    ticket_id = state["jira_ticket_id"]
    risk = (state.get("risk_level") or "MEDIUM").upper()

    if not research:
        return f"--- APPROVAL REQUIRED: GATE 1 ---\nTicket: {ticket_id}\nNo research output available."

    hypotheses = research.get("hypotheses", [])
    top = hypotheses[0] if hypotheses else {}
    files = research.get("affected_files", [])

    lines = [
        "--- APPROVAL REQUIRED: GATE 1 (RESEARCH REVIEW) ---",
        f"Ticket: {ticket_id}",
        f"Risk Level: {risk}",
        "",
        "Root Cause Hypothesis:",
        top.get("hypothesis", research.get("research_summary", "N/A")),
        "",
        "Affected Files (top candidates):",
    ]
    for f in files[:5]:
        lines.append(f"- {f.get('path', '?')}: {f.get('reason', '')}")

    lines.extend(
        [
            "",
            "Architectural Blast Radius:",
            ", ".join(research.get("module_blast_radius", [])) or "None identified",
            "",
            f"Research Confidence: {top.get('evidence_strength', 'unknown').upper()}",
        ]
    )
    if research.get("flags"):
        lines.append(f"Flags: {'; '.join(research['flags'])}")

    lines.extend(["", "Please respond with: APPROVE / REJECT / REVISE [notes]", "---"])
    return "\n".join(lines)


def format_gate2_request(state: AgentState) -> str:
    plan: PlanOutput | None = state.get("plan_output")
    ticket_id = state["jira_ticket_id"]
    risk = (plan.get("risk_level", "medium") if plan else "medium").upper()

    if not plan:
        return f"--- APPROVAL REQUIRED: GATE 2 ---\nTicket: {ticket_id}\nNo plan available."

    lines = [
        "--- APPROVAL REQUIRED: GATE 2 (EXECUTION PLAN) ---",
        f"Ticket: {ticket_id}",
        f"Risk Level: {risk}",
        "",
        "Problem:",
        plan.get("root_hypothesis", "N/A"),
        "",
        "Files to be Modified:",
    ]
    for f in plan.get("files_to_change", []):
        lines.append(f"- {f}")

    lines.extend(["", "Execution Steps:"])
    for step in plan.get("execution_steps", []):
        lines.append(
            f"{step.get('step_id')}. [{step.get('action')}] {step.get('file')} — {step.get('reason', '')}"
        )

    if plan.get("implementation_constraints"):
        lines.extend(["", "Constraints:"])
        for c in plan["implementation_constraints"]:
            lines.append(f"- {c}")

    if plan.get("self_healing_notes"):
        lines.extend(["", "Self-healing notes:"])
        for n in plan["self_healing_notes"]:
            lines.append(f"- {n}")

    lines.extend(["", "Please respond with: APPROVE / REJECT / REVISE [notes]", "---"])
    return "\n".join(lines)


def format_micro_confirm(state: AgentState) -> str:
    plan: PlanOutput | None = state.get("plan_output")
    ticket_id = state["jira_ticket_id"]

    if not plan:
        return f"--- QUICK CONFIRM ---\nTicket: {ticket_id}\nNo micro-plan available."

    lines = [
        "--- QUICK CONFIRM (MICRO ROUTE) ---",
        f"Ticket: {ticket_id}",
        "",
        "Proposed micro-fix:",
        plan.get("root_hypothesis", ""),
        "",
        "Files:",
    ]
    for f in plan.get("files_to_change", []):
        lines.append(f"- {f}")

    lines.extend(["", "Steps:"])
    for step in plan.get("execution_steps", []):
        lines.append(f"  {step.get('step_id')}. {step.get('action')} {step.get('file')}")

    lines.extend(["", "Confirm to proceed? APPROVE / REJECT / REVISE [notes]", "---"])
    return "\n".join(lines)
