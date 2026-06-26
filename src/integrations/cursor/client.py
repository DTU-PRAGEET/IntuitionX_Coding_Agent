"""Shared Cursor SDK client — local agent prompts (milestone 1.3)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from src.core.config import settings
from src.core.logging import get_logger

_log = get_logger("cursor_client")


@dataclass
class CursorRunResult:
    status: str
    text: str
    dry_run: bool = False
    exit_code: int = 0
    stderr: str = ""


def _status_to_exit_code(status: str) -> int:
    if status in ("finished", "completed", "dry_run", "success"):
        return 0
    return 1


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
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ImportError as e:
        raise RuntimeError("cursor-sdk not installed. Run: pip install cursor-sdk") from e

    _log.info("cursor_agent_start", cwd=str(resolved), model=model)
    result = Agent.prompt(
        prompt,
        AgentOptions(
            api_key=api_key,
            model=model,
            local=LocalAgentOptions(cwd=str(resolved)),
        ),
    )
    text = result.result or ""
    status = result.status or "unknown"
    exit_code = _status_to_exit_code(status)
    _log.info("cursor_agent_complete", status=status, text_len=len(text), exit_code=exit_code)
    return CursorRunResult(status=status, text=text, dry_run=False, exit_code=exit_code)
