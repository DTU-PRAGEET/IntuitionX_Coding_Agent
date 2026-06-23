"""Chainlit UI — Jira-to-MR pipeline with HITL gates and progress streaming."""

from __future__ import annotations

import asyncio
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

# Ensure project root is on sys.path when Chainlit loads this nested module.
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import chainlit as cl
from langgraph.types import Command

from src.core.config import settings
from src.core.formatters import format_gate1_request
from src.core.checkpointing import ensure_async_checkpointer
from src.core.graph import build_graph
from src.core.state import initial_state
from src.integrations.jira.client import fetch_ticket_by_id
from src.integrations.llm.sprinklr_router import SprinklrRouterError, probe_sprinklr_router
from src.pipeline.runner import PipelineError, run_pipeline #### only used for Mock mode. Runs simple pipeline.
from src.core.token_usage import start_pipeline_tracking, stop_pipeline_tracking
from src.tools.base import register_event_handler

_MOCK_MARKER = "[mock-security]"


def _chainlit_event_handler(event_type: str, payload: dict) -> None:
    log = cl.user_session.get("event_log") or []
    log.append({"type": event_type, **payload})
    cl.user_session.set("event_log", log[-30:])


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


def _resolve_repo_path(explicit: str | None) -> str | None:
    """Return a repo path only when the user explicitly provided one in chat."""
    if explicit:
        return str(Path(explicit).resolve())
    return None


def _default_repo_path() -> str:
    """Fallback repo for mock mode and local-only runs."""
    if settings.default_repo_path:
        return str(Path(settings.default_repo_path).resolve())
    return str(Path(".").resolve())


def _parse_user_input(text: str) -> tuple[str, str, str | None, bool]:
    """Return ticket_id, user_prompt, repo_path, mock_security."""
    text = text.strip()
    mock_security = False
    if text.lower().startswith("mock "):
        mock_security = True
        text = text[5:].strip()

    parts = text.split(maxsplit=1)
    jira_ticket_id = parts[0].upper()
    remainder = parts[1] if len(parts) > 1 else ""

    repo_path = None
    user_prompt = remainder
    repo_match = re.search(r"(/[\w./-]+)", remainder)
    if repo_match:
        repo_path = repo_match.group(1)
        user_prompt = remainder.replace(repo_path, "").strip()

    if mock_security:
        user_prompt = f"{_MOCK_MARKER} {user_prompt}".strip()

    return jira_ticket_id, user_prompt, repo_path, mock_security


_NODE_LABELS = {
    "orchestrator": "Orchestrator",
    "research": "Research",
    "research_hag": "Gate 1 (Research approval)",
    "planner": "Planner",
    "planning_hag": "Gate 2 (Plan approval)",
    "micro_confirm": "Micro confirm",
    "executor": "Executor",
}


async def _post_progress(message: str) -> None:
    await cl.Message(content=message).send()


def _format_token_usage(snapshot: dict[str, Any] | None) -> str:
    if not snapshot:
        return "**LLM token usage:** no data (tracking was not active or no LLM calls recorded)."
    total = snapshot.get("total_tokens", 0)
    if total == 0:
        return "**LLM token usage:** 0 tokens recorded."

    lines = [
        f"**LLM token usage:** {total:,} total "
        f"({snapshot.get('prompt_tokens', 0):,} prompt + "
        f"{snapshot.get('completion_tokens', 0):,} completion)"
    ]
    for agent, usage in (snapshot.get("agents") or {}).items():
        lines.append(
            f"- **{agent}:** {usage.get('total_tokens', 0):,} tokens "
            f"({usage.get('call_count', 0)} calls)"
        )
    return "\n".join(lines)


async def _show_token_usage(snapshot: dict[str, Any] | None) -> None:
    await cl.Message(content=_format_token_usage(snapshot)).send()


async def _run_mock_pipeline(
    ticket_id: str,
    repo_path: str,
    user_prompt: str,
) -> None:
    """Level 1 path — linear mock-security pipeline without HITL gates."""
    progress_queue: asyncio.Queue[tuple[str, dict[str, Any]]] = asyncio.Queue()

    def on_progress(event: str, payload: dict[str, Any]) -> None:
        progress_queue.put_nowait((event, payload))

    async def drain_progress() -> None:
        while True:
            try:
                event, payload = progress_queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if event == "node_start":
                await _post_progress(f"▶ **{_NODE_LABELS.get(payload.get('node', ''), payload.get('node', ''))}** started")
            elif event == "handoff":
                await _post_progress(
                    f"→ Handoff **{payload.get('source')}** → **{payload.get('target')}**"
                )
            elif event == "routing":
                await _post_progress(
                    f"**Routing:** {payload.get('route')} (risk: {payload.get('risk_level')})"
                )
            elif event == "complete":
                await _post_progress(
                    f"✓ **Pipeline complete** — {payload.get('steps')} executor steps, "
                    f"changed: `{', '.join(payload.get('changed_files', []))}`"
                )
            elif event == "token_usage":
                await _show_token_usage(payload)

    loop = asyncio.get_event_loop()

    async def runner() -> None:
        try:
            return await loop.run_in_executor(
                None,
                lambda: run_pipeline(
                    ticket_id,
                    repo_path,
                    mock_security=True,
                    user_prompt=user_prompt.replace(_MOCK_MARKER, "").strip(),
                    on_progress=on_progress,
                ),
            )
        except PipelineError:
            raise

    task = asyncio.create_task(runner())
    while not task.done():
        await drain_progress()
        await asyncio.sleep(0.2)
    await drain_progress()

    try:
        result = await task
    except PipelineError as e:
        await cl.Message(content=f"**Pipeline failed** at `{e.stage}`:\n```\n{e.message}\n```").send()
        return

    summary = {
        "success": result.success,
        "ticket_id": result.ticket_id,
        "routing_route": result.routing_route,
        "plan_steps": len(result.plan.execution_steps) if result.plan else 0,
        "executor_steps": result.execution.steps_completed if result.execution else 0,
        "changed_files": result.execution.changed_files if result.execution else [],
        "token_usage": result.token_usage,
    }
    await cl.Message(content=f"**Result**\n```json\n{json.dumps(summary, indent=2)}\n```").send()
    if result.token_usage:
        await _show_token_usage(result.token_usage)


async def _show_jira_ingestion(ticket_id: str) -> None:
    try:
        ticket, raw_payload, parsed = await asyncio.to_thread(fetch_ticket_by_id, ticket_id)
    except Exception as e:
        await cl.Message(content=f"**Jira fetch failed** for `{ticket_id}`:\n```\n{e}\n```").send()
        raise

    history = ticket.get("ticket_history_summary") or {}
    github_repo = history.get("github_repo") or (parsed.repo_links[0] if parsed.repo_links else "none")
    target_branch = history.get("target_branch") or "not specified"
    description = parsed.problem_statement or ticket.get("problem_statement") or ""
    acceptance = ticket.get("acceptance_criteria")

    acceptance_block = ""
    if acceptance and acceptance != "not_specified":
        if isinstance(acceptance, list):
            acceptance_block = "\n".join(f"- {item}" for item in acceptance)
        else:
            acceptance_block = str(acceptance)

    await cl.Message(
        content=(
            f"**Jira ticket** `{ticket_id}` — {parsed.title}\n\n"
            f"**GitHub repo:** `{github_repo}`\n"
            f"**Target branch:** `{target_branch}`\n"
            f"**Labels:** {', '.join(parsed.labels[:8]) or 'none'}\n\n"
            f"**Full description** ({len(description)} chars)\n"
            f"```\n{description[:8000]}\n```\n\n"
            + (
                f"**Acceptance criteria**\n```\n{acceptance_block[:4000]}\n```\n\n"
                if acceptance_block
                else ""
            )
            + f"**Raw API snapshot** (description_preview is first 500 chars only)\n"
            f"```json\n{json.dumps(raw_payload, indent=2)}\n```"
        )
    ).send()


async def _show_workflow_result(result: dict) -> None:
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
    if ticket.get("repo_links"):
        summary_lines.append(f"**Repo links:** {', '.join(ticket['repo_links'][:3])}")

    if summary_lines:
        await cl.Message(content="\n".join(summary_lines)).send()

    if result.get("exit_message"):
        await cl.Message(content=result["exit_message"]).send()
        return

    if result.get("execution_output"):
        ex = result["execution_output"]
        await cl.Message(
            content=(
                f"**Pull Request:** {ex.get('mr_url') or 'N/A'}\n\n"
                f"**Branch:** {ex.get('branch_name', '')}\n\n"
                f"**Worktree:** {ex.get('worktree_path', '')}\n\n"
                f"**Changed files:** {', '.join(ex.get('changed_files', []))}"
            )
        ).send()
        return

    if result.get("current_error_log"):
        await cl.Message(
            content=f"**Execution failed:**\n```\n{result['current_error_log']}\n```"
        ).send()


async def _show_event_log() -> None:
    log = cl.user_session.get("event_log") or []
    tool_events = [e for e in log if e.get("type") in ("tool_start", "tool_end")][-8:]
    if tool_events:
        lines = []
        for e in tool_events:
            if e["type"] == "tool_start":
                lines.append(f"- `{e.get('tool_name')}` started")
            else:
                lines.append(
                    f"- `{e.get('tool_name')}` done ({e.get('latency_ms', 0)}ms)"
                    + (" **ERROR**" if e.get("is_error") else "")
                )
        await cl.Message(content="**Tool activity:**\n" + "\n".join(lines)).send()


async def _run_graph_with_hitl(graph, state, config):
    current_input = state

    while True:
        interrupted = False
        interrupt_payload = None
        final_state = None

        async for event in graph.astream(current_input, config=config, stream_mode="updates"):
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
                            repo = node_output.get("repo_path")
                            if repo:
                                detail += f"\nRepo: `{repo}`"
                            branch = node_output.get("git_target_branch")
                            if branch:
                                detail += f" (base: `{branch}`)"
                        elif node_name == "research":
                            preview_state = {**(final_state or state), **node_output}
                            gate_preview = format_gate1_request(preview_state)
                            detail = f"\n\n```\n{gate_preview[:3500]}\n```"

                        await cl.Message(content=f"✓ **{label}** complete{detail}").send()

        if interrupted and interrupt_payload:
            gate = interrupt_payload.get("gate", "GATE")
            msg = interrupt_payload.get("message", "Approval required.")

            await cl.Message(content=f"**{gate}**\n```\n{msg[:4000]}\n```").send()

            prompt = (
                f"**{gate}** — respond with **APPROVE**, **REJECT** [feedback], or **REVISE** [notes]:"
            )
            res = await cl.AskUserMessage(content=prompt, timeout=3600).send()

            if not res:
                return final_state or state

            decision = _parse_decision(res["output"])
            if decision.get("status") == "REJECT" and not decision.get("feedback"):
                await cl.Message(content="Rejected without feedback — workflow ended.").send()
                return final_state or state

            current_input = Command(resume=decision)
            continue

        return final_state or state


@cl.on_chat_start
async def on_chat_start():
    cl.user_session.set("thread_id", str(uuid.uuid4()))
    cl.user_session.set("event_log", [])

    llm_note = ""
    if settings.llm_provider == "sprinklr":
        try:
            await asyncio.to_thread(probe_sprinklr_router)
            llm_note = "\n\n**LLM:** Sprinklr router connected."
        except SprinklrRouterError as e:
            llm_note = (
                f"\n\n**LLM warning:** Sprinklr router unreachable — {e}\n"
                "Check VPN, `SPRINKLR_LLM_CLIENT_IDENTIFIER`, and network access."
            )
        except Exception as e:
            llm_note = f"\n\n**LLM warning:** Sprinklr probe failed — {e}"

    await cl.Message(
        content=(
            "**Sprinklr Jira Solver**\n\n"
            "Submit a Jira ticket ID to run the full pipeline with approval gates.\n\n"
            "**Examples:**\n"
            "- `PLAT-1234` — real Jira ticket\n"
            "- `mock SEC-4421` — mock security demo (no gates, fast path)\n"
            "- `mock SEC-4421 /path/to/repo` — mock demo with explicit repo\n\n"
            "Format: `[mock] JIRA-ID [optional prompt] [optional /repo/path]`"
            f"{llm_note}"
        )
    ).send()


@cl.on_message
async def on_message(message: cl.Message):
    text = message.content.strip()
    if not text:
        await cl.Message(content="Please provide a Jira ID to begin.").send()
        return

    if not re.match(r"^(mock\s+)?[A-Z]+-\d+", text, re.I):
        await cl.Message(content="Invalid format. Start with a Jira ID like `PLAT-1234` or `mock SEC-4421`.").send()
        return

    jira_ticket_id, user_prompt, repo_path_arg, mock_security = _parse_user_input(text)
    explicit_repo = _resolve_repo_path(repo_path_arg)

    if mock_security:
        repo_path = explicit_repo or _default_repo_path()
        if not Path(repo_path).exists():
            await cl.Message(content=f"**Repo path not found:** `{repo_path}`").send()
            return
        await cl.Message(
            content=f"**Mock mode** — running linear pipeline for `{jira_ticket_id}` in `{repo_path}`"
        ).send()
        await _run_mock_pipeline(jira_ticket_id, repo_path, user_prompt)
        await _show_event_log()
        return

    if explicit_repo and not Path(explicit_repo).exists():
        await cl.Message(content=f"**Repo path not found:** `{explicit_repo}`").send()
        return

    thread_id = str(uuid.uuid4())
    cl.user_session.set("thread_id", thread_id)
    config = {"configurable": {"thread_id": thread_id}}

    await cl.Message(content=f"Fetching Jira ticket **{jira_ticket_id}**…").send()
    try:
        await _show_jira_ingestion(jira_ticket_id)
    except Exception:
        return

    checkpointer = await ensure_async_checkpointer()
    graph = build_graph(checkpointer=checkpointer)
    state = initial_state(
        jira_ticket_id=jira_ticket_id,
        user_prompt=user_prompt,
        repo_path=explicit_repo,
    )

    repo_hint = explicit_repo or "from Jira ticket GitHub link"
    await cl.Message(
        content=(
            f"Starting workflow for **{jira_ticket_id}** (thread: `{thread_id[:8]}…`)\n"
            f"Research repo: `{repo_hint}`"
        )
    ).send()

    tracker = start_pipeline_tracking()
    token_snapshot: dict[str, Any] | None = None
    try:
        result = await _run_graph_with_hitl(graph, state, config)
    except Exception as e:
        await cl.Message(content=f"**Workflow error:** {e}").send()
        return
    finally:
        active = stop_pipeline_tracking() or tracker
        token_snapshot = active.to_dict()
        active.log_summary()

    await _show_event_log()
    await _show_workflow_result(result)
    await _show_token_usage(token_snapshot)
