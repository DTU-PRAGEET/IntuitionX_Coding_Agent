"""Centralized LLM token usage tracking for pipeline runs."""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass, field
from typing import Any, Mapping

from src.core.logging import get_logger

_log = get_logger("token_usage")

_current_tracker: ContextVar["TokenUsageTracker | None"] = ContextVar(
    "pipeline_token_tracker", default=None
)
# Fallback for worker threads (LangGraph sync nodes, run_in_executor) where ContextVar
# does not inherit the parent asyncio context.
_global_tracker: TokenUsageTracker | None = None

_tracing_agent: ContextVar[str | None] = ContextVar("tracing_agent", default=None)


@dataclass
class AgentTokenUsage:
    """Per-agent prompt/completion totals inside a pipeline run.
    Aggregated by `TokenUsageTracker.record`; exported in `PipelineResult.token_usage`."""
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    call_count: int = 0

    def add(self, prompt: int, completion: int, total: int | None = None) -> None:
        """Add for AgentTokenUsage."""
        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total if total is not None else prompt + completion
        self.call_count += 1


@dataclass
class TokenUsageTracker:
    """Accumulates token usage per agent for one pipeline execution."""

    agents: dict[str, AgentTokenUsage] = field(default_factory=dict)

    def record(self, agent: str, prompt: int, completion: int, total: int | None = None) -> None:
        """Record for TokenUsageTracker."""
        if prompt == 0 and completion == 0 and (total or 0) == 0:
            return
        usage = self.agents.setdefault(agent, AgentTokenUsage())
        usage.add(prompt, completion, total)
        _log.info(
            "token_usage",
            agent=agent,
            prompt=prompt,
            completion=completion,
            total=total if total is not None else prompt + completion,
        )

    @property
    def prompt_tokens(self) -> int:
        """Prompt Tokens for TokenUsageTracker."""
        return sum(u.prompt_tokens for u in self.agents.values())

    @property
    def completion_tokens(self) -> int:
        """Completion Tokens for TokenUsageTracker."""
        return sum(u.completion_tokens for u in self.agents.values())

    @property
    def total_tokens(self) -> int:
        """Total Tokens for TokenUsageTracker."""
        return sum(u.total_tokens for u in self.agents.values())

    def log_summary(self) -> None:
        """Log Summary for TokenUsageTracker."""
        _log.info("token_usage_summary_begin", separator="-" * 50)
        for agent, usage in sorted(self.agents.items()):
            _log.info(
                "token_usage_agent",
                agent=agent,
                prompt_tokens=usage.prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=usage.total_tokens,
                call_count=usage.call_count,
            )
        _log.info(
            "pipeline_total_llm_usage",
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
        )
        _log.info("token_usage_summary_end", separator="-" * 50)

    def to_dict(self) -> dict[str, Any]:
        """Serializable snapshot for API/CLI/UI responses."""
        return {
            "agents": {
                name: {
                    "prompt_tokens": usage.prompt_tokens,
                    "completion_tokens": usage.completion_tokens,
                    "total_tokens": usage.total_tokens,
                    "call_count": usage.call_count,
                }
                for name, usage in sorted(self.agents.items())
            },
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


def get_tracker() -> TokenUsageTracker | None:
    """Return tracker."""
    return _current_tracker.get() or _global_tracker


def start_pipeline_tracking() -> TokenUsageTracker:
    """Start pipeline tracking."""
    global _global_tracker
    tracker = TokenUsageTracker()
    _current_tracker.set(tracker)
    _global_tracker = tracker
    return tracker


def stop_pipeline_tracking() -> TokenUsageTracker | None:
    """Stop pipeline tracking."""
    global _global_tracker
    tracker = _current_tracker.get() or _global_tracker
    _current_tracker.set(None)
    _global_tracker = None
    return tracker


def set_tracing_agent(agent: str) -> Token:
    """Mark which pipeline agent is active for nested Cursor/LLM usage attribution."""
    return _tracing_agent.set(agent)


def reset_tracing_agent(token: Token) -> None:
    """Restore tracing agent ContextVar after a scoped agent run."""
    _tracing_agent.reset(token)


def get_tracing_agent() -> str | None:
    """Return tracing agent."""
    return _tracing_agent.get()


def extract_usage_mapping(usage: Mapping[str, Any]) -> tuple[int, int, int]:
    """Normalize token usage dict (camelCase or snake_case) to (prompt, completion, total)."""
    prompt = int(
        usage.get("input_tokens")
        or usage.get("inputTokens")
        or usage.get("prompt_tokens")
        or usage.get("promptTokens")
        or 0
    )
    completion = int(
        usage.get("output_tokens")
        or usage.get("outputTokens")
        or usage.get("completion_tokens")
        or usage.get("completionTokens")
        or 0
    )
    total = int(usage.get("total_tokens") or usage.get("totalTokens") or 0)
    if total == 0 and (prompt or completion):
        total = prompt + completion
    return prompt, completion, total


def _accumulate_langsmith_usage(run_tree: Any) -> tuple[int, int, int]:
    """Sum token usage from a LangSmith run tree (self + child LLM runs)."""
    if run_tree is None:
        return 0, 0, 0

    prompt = completion = total = 0
    outputs = getattr(run_tree, "outputs", None) or {}
    if isinstance(outputs, dict):
        for key in ("usage", "token_usage", "usage_metadata"):
            nested = outputs.get(key)
            if isinstance(nested, dict) and nested:
                p, c, t = extract_usage_mapping(nested)
                prompt += p
                completion += c
                total += t

    for child in getattr(run_tree, "child_runs", None) or []:
        p, c, t = _accumulate_langsmith_usage(child)
        prompt += p
        completion += c
        total += t

    if total == 0 and (prompt or completion):
        total = prompt + completion
    return prompt, completion, total


def report_agent_token_usage(agent: str | None = None) -> None:
    """Read LangSmith current run tree and record token usage for the active agent."""
    tracker = get_tracker()
    if tracker is None:
        return
    resolved = agent or get_tracing_agent() or "unknown"
    try:
        from langsmith import get_current_run_tree

        run = get_current_run_tree()
    except Exception:
        return
    prompt, completion, total = _accumulate_langsmith_usage(run)
    if prompt or completion or total:
        tracker.record(resolved, prompt, completion, total)


def report_cursor_usage(usage: Mapping[str, Any] | None) -> None:
    """Record Cursor SDK turn usage under the active tracing agent."""
    if not usage:
        return
    tracker = get_tracker()
    if tracker is None:
        return
    agent = get_tracing_agent() or "cursor"
    prompt, completion, total = extract_usage_mapping(dict(usage))
    tracker.record(agent, prompt, completion, total)


def extract_token_usage(response: Any) -> tuple[int, int, int]:
    """Extract (prompt, completion, total) from a LangChain LLM response."""
    prompt = completion = total = 0

    usage_meta = getattr(response, "usage_metadata", None)
    if isinstance(usage_meta, dict) and usage_meta:
        prompt = int(
            usage_meta.get("input_tokens")
            or usage_meta.get("prompt_tokens")
            or 0
        )
        completion = int(
            usage_meta.get("output_tokens")
            or usage_meta.get("completion_tokens")
            or 0
        )
        total = int(usage_meta.get("total_tokens") or prompt + completion)

    response_meta = getattr(response, "response_metadata", None)
    if response_meta is None and isinstance(response, dict):
        response_meta = response.get("response_metadata") or {}
        if not usage_meta and isinstance(response.get("usage_metadata"), dict):
            usage_meta = response["usage_metadata"]
            prompt = int(
                usage_meta.get("input_tokens")
                or usage_meta.get("prompt_tokens")
                or prompt
                or 0
            )
            completion = int(
                usage_meta.get("output_tokens")
                or usage_meta.get("completion_tokens")
                or completion
                or 0
            )
            total = int(usage_meta.get("total_tokens") or prompt + completion or total)

    if isinstance(response_meta, dict):
        token_usage = (
            response_meta.get("token_usage")
            or response_meta.get("usage")
            or {}
        )
        if isinstance(token_usage, dict) and token_usage:
            prompt = int(
                token_usage.get("prompt_tokens")
                or token_usage.get("input_tokens")
                or prompt
                or 0
            )
            completion = int(
                token_usage.get("completion_tokens")
                or token_usage.get("output_tokens")
                or completion
                or 0
            )
            total = int(
                token_usage.get("total_tokens") or prompt + completion or total or 0
            )

    if total == 0 and (prompt or completion):
        total = prompt + completion
    return prompt, completion, total


def record_llm_response(agent: str, response: Any) -> None:
    """Record llm response."""
    tracker = get_tracker()
    if tracker is None:
        return
    prompt, completion, total = extract_token_usage(response)
    tracker.record(agent, prompt, completion, total)
    if total > 0:
        from src.api.metrics_store import record_token_usage

        record_token_usage(agent, total)


def record_messages_usage(agent: str, messages: list[Any]) -> None:
    """Sum usage_metadata from AIMessage objects (e.g. deepagents research loop)."""
    tracker = get_tracker()
    if tracker is None:
        return
    for msg in messages:
        if isinstance(msg, dict):
            role = msg.get("type") or msg.get("role")
            if role in ("ai", "assistant"):
                record_llm_response(agent, msg)
            continue
        if getattr(msg, "type", None) == "ai" or msg.__class__.__name__ == "AIMessage":
            record_llm_response(agent, msg)
