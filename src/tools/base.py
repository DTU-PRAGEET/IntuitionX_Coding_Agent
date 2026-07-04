"""Hardened tool base — ResultBuffer, audit decorator, ToolDefinition."""

from __future__ import annotations

import functools
import tempfile
import time
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Callable, Optional, Type

from pydantic import BaseModel

from src.core.logging import get_logger
from src.core.schemas import ToolResult
from src.tools.path_utils import resolve_path_sandbox
from src.tools.truncate import truncate_head

TRUNCATE_LIMIT = 1000
_event_handlers: list[Callable[[str, dict], None]] = []
_log = get_logger("tool_base")


def register_event_handler(handler: Callable[[str, dict], None]) -> None:
    """Register Event Handler for this module."""
    _event_handlers.append(handler)


def emit_event(event_type: str, payload: dict) -> None:
    """Emit event."""
    for handler in _event_handlers:
        try:
            handler(event_type, payload)
        except Exception:
            pass


class ResultBuffer:
    """Truncate tool output and spill full content to a temp log file."""

    def __init__(self, limit: int = TRUNCATE_LIMIT):
        """Initialize ResultBuffer with the provided arguments."""
        self.limit = limit

    def process(self, raw: str, tool_name: str) -> tuple[str, bool, Optional[str]]:
        """Process for ResultBuffer."""
        head = truncate_head(raw, max_lines=2000, max_bytes=self.limit)
        if not head.truncated and len(raw) <= self.limit:
            return raw, False, None
        log_path = tempfile.mktemp(prefix=f"tool_{tool_name}_", suffix=".log")
        Path(log_path).write_text(raw, encoding="utf-8")
        preview = head.content
        if len(preview) > self.limit:
            preview = preview[: self.limit]
        if head.truncated or len(raw) > self.limit:
            preview = preview + f"\n[Result Truncated: use log_file_path for full output]"
        return preview, True, log_path


def audit_tool(func: Callable) -> Callable:
    """Wrap tool execute() with timing, events, and structured ToolResult."""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs) -> ToolResult:
        """Wrapper for this module."""
        tool_name = getattr(self, "name", func.__name__)
        log = get_logger(tool_name)
        log.info("invoke", args={k: str(v)[:200] for k, v in kwargs.items()})
        emit_event("tool_start", {"tool_name": tool_name, "args": str(kwargs or args)[:200]})
        start = time.monotonic()
        try:
            result: ToolResult = func(self, *args, **kwargs)
            result.latency_ms = int((time.monotonic() - start) * 1000)
            result.tool_name = tool_name
            if result.is_error:
                log.error("failed", exit_code=result.exit_code, output=result.output[:200])
            else:
                log.info("success", exit_code=result.exit_code, latency_ms=result.latency_ms)
        except Exception as e:
            latency = int((time.monotonic() - start) * 1000)
            log.error("exception", error=str(e), latency_ms=latency)
            result = ToolResult(
                output=str(e),
                exit_code=1,
                is_error=True,
                latency_ms=latency,
                tool_name=tool_name,
            )
        emit_event(
            "tool_end",
            {
                "tool_name": tool_name,
                "exit_code": result.exit_code,
                "is_error": result.is_error,
                "truncated": result.truncated,
                "latency_ms": result.latency_ms,
            },
        )
        return result

    return wrapper


class ToolDefinition(ABC):
    """Base class for all hardened tools."""

    name: str = "base"
    input_schema: Type[BaseModel] = BaseModel

    def __init__(self, cwd: str):
        """Initialize ToolDefinition with the provided arguments."""
        self.cwd = str(Path(cwd).resolve())
        if not Path(self.cwd).exists():
            Path(self.cwd).mkdir(parents=True, exist_ok=True)
        self._buffer = ResultBuffer()

    def resolve_path(self, path: str) -> Path:
        """Resolve Path for ToolDefinition."""
        return resolve_path_sandbox(path, self.cwd)

    def _wrap_output(self, raw: str, exit_code: int = 0, is_error: bool = False) -> ToolResult:
        """Wrap Output for ToolDefinition."""
        output, truncated, log_path = self._buffer.process(raw, self.name)
        return ToolResult(
            output=output,
            exit_code=exit_code,
            is_error=is_error,
            truncated=truncated,
            log_file_path=log_path,
            tool_name=self.name,
        )

    @abstractmethod
    def execute(self, **kwargs: Any) -> ToolResult:
        """Run the tool with validated arguments and return a structured result."""
        ...
