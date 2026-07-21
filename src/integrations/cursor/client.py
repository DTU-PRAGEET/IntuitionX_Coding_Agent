"""Shared Cursor SDK client — local agent prompts (milestone 1.3)."""

from __future__ import annotations

import json
import os
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from src.core.config import settings
from src.core.logging import get_logger
from src.core.token_usage import extract_usage_mapping

_log = get_logger("cursor_client")

_DEBUG_LOG_PATH = (
    Path(__file__).resolve().parents[3] / ".cursor" / "debug-7f2b04.log"
)
_DEBUG_SESSION_ID = "7f2b04"
_BRIDGE_AUTH_PATCHED = False


def _debug_log(*, hypothesis_id: str, location: str, message: str, data: dict) -> None:
    # region agent log
    try:
        payload = {
            "sessionId": _DEBUG_SESSION_ID,
            "hypothesisId": hypothesis_id,
            "location": location,
            "message": message,
            "data": data,
            "timestamp": int(time.time() * 1000),
        }
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with _DEBUG_LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload) + "\n")
    except OSError:
        pass
    # endregion


def _ensure_bridge_safe_auth_tokens() -> None:
    """Avoid cursor-sdk-bridge argv tokens that start with '-' (CLI parser treats them as flags)."""
    global _BRIDGE_AUTH_PATCHED
    if _BRIDGE_AUTH_PATCHED:
        return

    from cursor_sdk import _store_callback as store_cb
    from cursor_sdk import _tool_callback as tool_cb

    def _safe_auth_token() -> str:
        while True:
            token = secrets.token_urlsafe(32)
            if not token.startswith("-"):
                return token

    tool_cb._new_auth_token = _safe_auth_token
    store_cb._new_auth_token = _safe_auth_token
    _BRIDGE_AUTH_PATCHED = True
    _debug_log(
        hypothesis_id="H1",
        location="client.py:_ensure_bridge_safe_auth_tokens",
        message="patched cursor-sdk callback auth token generation",
        data={"patched_modules": ["_tool_callback", "_store_callback"]},
    )

_EMPTY_USAGE = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}


@dataclass
class CursorRunResult:
    """Raw result from `client.run_local_agent` — status, text, stderr, token usage.
    Wrapped by `CursorGateway.ask` / `.search` before agents see structured output."""
    status: str
    text: str
    dry_run: bool = False
    exit_code: int = 0
    stderr: str = ""
    usage: dict[str, int] = field(default_factory=lambda: dict(_EMPTY_USAGE))


def _status_to_exit_code(status: str) -> int:
    """Status To Exit Code for this module."""
    if status in ("finished", "completed", "dry_run", "success"):
        return 0
    return 1


def _collect_usage_from_stream(run: Any) -> dict[str, int]:
    """Accumulate token usage from turn-ended interaction updates in a run stream."""
    usage = dict(_EMPTY_USAGE)
    for event in run.events():
        update = getattr(event, "interaction_update", None)
        if update is None or getattr(update, "type", None) != "turn-ended":
            continue
        turn_usage = getattr(update, "usage", None)
        if not turn_usage:
            continue
        prompt, completion, total = extract_usage_mapping(dict(turn_usage))
        usage["input_tokens"] += prompt
        usage["output_tokens"] += completion
        usage["total_tokens"] += total
    return usage


def run_local_agent(
    prompt: str,
    *,
    cwd: Path,
    dry_run: bool = False,
    model: str | None = None,
) -> CursorRunResult:
    """Run a one-shot Cursor local agent; grep/read/web tools are provided by the harness."""
    api_key = (os.environ.get("CURSOR_API_KEY") or settings.cursor_api_key).strip()
    resolved = cwd.resolve()
    model = (model or settings.cursor_model).strip() or "composer-2.5"

    if dry_run or not api_key:
        _log.info(
            "cursor_agent_dry_run",
            cwd=str(resolved),
            has_api_key=bool(api_key),
            prompt_len=len(prompt),
        )
        return CursorRunResult(status="dry_run", text="", dry_run=True, exit_code=0)

    try:
        from cursor_sdk import Agent, LocalAgentOptions
    except ImportError as e:
        raise RuntimeError("cursor-sdk not installed. Run: pip install cursor-sdk") from e

    _ensure_bridge_safe_auth_tokens()
    _debug_log(
        hypothesis_id="H2",
        location="client.py:run_local_agent",
        message="starting cursor agent",
        data={"cwd": str(resolved), "model": model, "has_api_key": bool(api_key)},
    )
    _log.info("cursor_agent_start", cwd=str(resolved), model=model)
    from src.api.metrics_store import record_cursor_request

    record_cursor_request()
    try:
        with Agent.create(
            model=model,
            api_key=api_key,
            local=LocalAgentOptions(cwd=str(resolved)),
        ) as agent:
            run = agent.send(prompt)
            usage = _collect_usage_from_stream(run)
            result = run.wait()
    except Exception as exc:
        _debug_log(
            hypothesis_id="H1",
            location="client.py:run_local_agent",
            message="cursor agent bridge launch failed",
            data={"error_type": type(exc).__name__, "error": str(exc)[:300]},
        )
        raise
    _debug_log(
        hypothesis_id="H1",
        location="client.py:run_local_agent",
        message="cursor agent bridge launch succeeded",
        data={"status": getattr(result, "status", "unknown")},
    )
    text = result.result or ""
    status = result.status or "unknown"
    exit_code = _status_to_exit_code(status)
    _log.info(
        "cursor_agent_complete",
        status=status,
        text_len=len(text),
        exit_code=exit_code,
        input_tokens=usage["input_tokens"],
        output_tokens=usage["output_tokens"],
    )
    return CursorRunResult(
        status=status,
        text=text,
        dry_run=False,
        exit_code=exit_code,
        usage=usage,
    )
