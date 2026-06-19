"""Chainlit UI — entry point, HITL surface, tool event display."""

from __future__ import annotations

import re
import uuid

import chainlit as cl
from langgraph.types import Command

from src.core.graph import build_graph
from src.core.state import initial_state
from src.tools.base import register_event_handler

# Event bus → Chainlit session log
def _chainlit_event_handler(event_type: str, payload: dict) -> None:
    log = cl.user_session.get("event_log") or []
    log.append({"type": event_type, **payload})
    cl.user_session.set("event_log", log[-20:])


register_event_handler(_chainlit_event_handler)


def _parse_decision(text: str) -> dict:
    upper = text.strip().upper()
    if upper.startswith("APPROVE"):
        return {"status": "APPROVE", "feedback": text[7:].strip() or None}
    if upper.startswith("REJECT"):
        return {"status": "REJECT", "feedback": text[6:].strip() or None}
    if upper.startswith("REVISE"):
        return {"status": "REVISE", "feedback": text[6:].strip() or text}
    if upper in ("YES", "Y", "OK", "CONFIRM"):
        return {"status": "APPROVE"}
    return {"status": "REVISE", "feedback": text}


@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("thread_id", str(uuid.uuid4()))
    cl.user_session.set("event_log", [])
    await cl.Message(
        content=(
            "**Sprinklr Jira Solver**\n\n"
            "Submit a Jira ticket to begin:\n"
            "`PLAT-1234 Fix null check in PaymentService.java`\n\n"
            "Format: `JIRA-ID [optional prompt] [optional repo path]`"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    text = message.content.strip()
    if not text:
        await cl.Message(content="Please provide a Jira ID to begin.").send()
        return

    parts = text.split(maxsplit=1)
    jira_id_match = re.match(r"^[A-Z]+-\d+", parts[0], re.I)
    if not jira_id_match:
        await cl.Message(content="Invalid format. Start with a Jira ID like PLAT-1234.").send()
        return

    jira_ticket_id = parts[0].upper()
    remainder = parts[1] if len(parts) > 1 else ""

    repo_path = None
    user_prompt = remainder
    repo_match = re.search(r"(/[\w./-]+)", remainder)
    if repo_match:
        repo_path = repo_match.group(1)
        user_prompt = remainder.replace(repo_path, "").strip()

    thread_id = str(uuid.uuid4())
    cl.user_session.set("thread_id", thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    graph = build_graph()
    state = initial_state(
        jira_ticket_id=jira_ticket_id,
        user_prompt=user_prompt,
        repo_path=repo_path,
    )

    await cl.Message(content=f"Starting workflow for **{jira_ticket_id}** (thread: `{thread_id[:8]}...`)").send()

    try:
        result = await _run_graph_with_hitl(graph, state, config)
    except Exception as e:
        await cl.Message(content=f"Workflow error: {e}").send()
        return

    await _show_event_log()
    await _show_workflow_result(result)


async def _show_workflow_result(result: dict) -> None:
    """Summarize final workflow state for the user."""
    route = result.get("routing_route")
    phase = result.get("current_phase")
    ticket = result.get("jira_ticket") or {}

    summary_lines = []
    if ticket.get("title"):
        summary_lines.append(f"**Ticket:** {ticket.get('title')} ({ticket.get('status', 'unknown')})")
    if route:
        summary_lines.append(f"**Route:** {route}")
    if phase:
        summary_lines.append(f"**Phase:** {phase}")
    if ticket.get("flags"):
        summary_lines.append(f"**Flags:** {', '.join(ticket['flags'][:3])}")

    if summary_lines:
        await cl.Message(content="\n".join(summary_lines)).send()

    if result.get("exit_message"):
        await cl.Message(content=result["exit_message"]).send()
        return

    if result.get("execution_output"):
        ex = result["execution_output"]
        await cl.Message(
            content=(
                f"**MR Created:** {ex.get('mr_url', 'N/A')}\n\n"
                f"**Branch:** {ex.get('branch_name', '')}\n\n"
                f"**Changed files:** {', '.join(ex.get('changed_files', []))}"
            )
        ).send()
        return

    if result.get("current_error_log"):
        await cl.Message(
            content=f"Execution failed:\n```\n{result['current_error_log']}\n```"
        ).send()
        return

    if result.get("research_output") and phase == "RESEARCH":
        ro = result["research_output"]
        await cl.Message(
            content=(
                f"**Research summary:**\n{ro.get('research_summary', 'N/A')[:2000]}\n\n"
                "Waiting for approval at Gate 1 — if you do not see a prompt, reply **APPROVE** or **REJECT**."
            )
        ).send()
        return

    if route == "EXIT":
        await cl.Message(
            content="Workflow exited. The orchestrator chose EXIT — see route summary above."
        ).send()
        return

    await cl.Message(
        content=(
            "Workflow stopped early. Common causes:\n"
            "- **REPO_PATH** not set in `.env.local`\n"
            "- HITL gate not reached (restart Chainlit after code changes)\n"
            "- Orchestrator or agent error without exit_message"
        )
    ).send()


async def _show_event_log():
    log = cl.user_session.get("event_log") or []
    tool_events = [e for e in log if e.get("type") in ("tool_start", "tool_end")][-6:]
    if tool_events:
        lines = []
        for e in tool_events:
            if e["type"] == "tool_start":
                lines.append(f"- `{e.get('tool_name')}` started")
            else:
                lines.append(
                    f"- `{e.get('tool_name')}` done ({e.get('latency_ms', 0)}ms)"
                    + (" ERROR" if e.get("is_error") else "")
                )
        await cl.Message(content="**Tool activity:**\n" + "\n".join(lines)).send()


_NODE_LABELS = {
    "orchestrator": "Orchestrator",
    "research": "Research",
    "research_hag": "Gate 1 (Research approval)",
    "planner": "Planner",
    "planning_hag": "Gate 2 (Plan approval)",
    "micro_confirm": "Micro confirm",
    "executor": "Executor",
}


async def _run_graph_with_hitl(graph, state, config):
    """Run graph, handling interrupts via Chainlit user input."""
    current_input = state

    while True:
        interrupted = False
        interrupt_payload = None
        final_state = None

        for event in graph.stream(current_input, config=config, stream_mode="updates"):
            for node_name, node_output in event.items():
                if node_name == "__interrupt__":
                    interrupted = True
                    interrupts = node_output
                    if interrupts:
                        interrupt_payload = (
                            interrupts[0].value
                            if hasattr(interrupts[0], "value")
                            else interrupts[0]
                        )
                    break
                if isinstance(node_output, dict):
                    final_state = {**(final_state or state), **node_output}
                    label = _NODE_LABELS.get(node_name)
                    if label:
                        detail = ""
                        if node_name == "orchestrator":
                            route = node_output.get("routing_route")
                            if route:
                                detail = f" → route **{route}**"
                        await cl.Message(content=f"✓ **{label}** complete{detail}").send()

        if interrupted and interrupt_payload:
            gate = interrupt_payload.get("gate", "GATE")
            msg = interrupt_payload.get("message", "Approval required.")
            await cl.Message(content=f"**{gate}**\n```\n{msg[:4000]}\n```").send()

            prompt = (
                "Respond with **APPROVE**, **REJECT**, or **REVISE** [notes]:"
                if gate != "DIFF_CONFIRM"
                else "Diff preview above. **APPROVE** to commit and raise MR, or **REJECT**:"
            )
            res = await cl.AskUserMessage(content=prompt, timeout=3600).send()

            if not res:
                return final_state or state

            decision = _parse_decision(res["output"])
            current_input = Command(resume=decision)
            continue

        return final_state or state
