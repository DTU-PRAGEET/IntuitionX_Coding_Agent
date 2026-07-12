"""Shared utilities for thin agent runners."""

from __future__ import annotations

from typing import Any, Type, TypeVar

from pydantic import BaseModel

from src.core.token_usage import record_llm_response

T = TypeVar("T", bound=BaseModel)


def _flatten_content_blocks(raw: object) -> str:
    """Normalize LLM content — Gemini returns list[dict] blocks, not always str."""
    if raw is None:
        return ""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, list):
        parts: list[str] = []
        for block in raw:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                text = block.get("text")
                if text is None:
                    text = block.get("content")
                if text is not None:
                    parts.append(str(text))
        return "\n".join(parts)
    return str(raw)


def _json_scan_state(raw: str) -> tuple[bool, bool, list[str]]:
    """Return (in_string, escape_next, open_brackets) for a JSON fragment."""
    in_string = False
    escape = False
    stack: list[str] = []
    for ch in raw:
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("}")
        elif ch == "[":
            stack.append("]")
        elif ch in "}]" and stack and stack[-1] == ch:
            stack.pop()
    return in_string, escape, stack


def _sanitize_json_string_literals(raw: str) -> str:
    """Escape raw control characters inside JSON string literals."""
    out: list[str] = []
    in_string = False
    escape = False
    for ch in raw:
        if not in_string:
            out.append(ch)
            if ch == '"':
                in_string = True
            continue
        if escape:
            out.append(ch)
            escape = False
            continue
        if ch == "\\":
            out.append(ch)
            escape = True
            continue
        if ch == '"':
            out.append(ch)
            in_string = False
            continue
        if ch == "\n":
            out.append("\\n")
            continue
        if ch == "\r":
            out.append("\\r")
            continue
        if ch == "\t":
            out.append("\\t")
            continue
        if ord(ch) < 0x20:
            out.append(f"\\u{ord(ch):04x}")
            continue
        out.append(ch)
    return "".join(out)


def _balance_json_brackets(raw: str) -> str:
    """Close unclosed strings/arrays/objects in truncated JSON."""
    in_string, _, stack = _json_scan_state(raw)
    out = raw
    if in_string:
        out += '"'
    while stack:
        out += stack.pop()
    return out


def _extract_json_object(content: str) -> str:
    """Extract the first top-level JSON object, including truncated responses."""
    start = content.find("{")
    if start < 0:
        raise ValueError(f"Could not parse JSON from LLM response: {content[:200]}")
    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(content)):
        ch = content[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[start : idx + 1]
    return content[start:]


def _parse_json_candidates(raw_json: str, model: Type[T]) -> T:
    """Try direct, sanitized, and bracket-balanced parses."""
    candidates = [raw_json]
    sanitized = _sanitize_json_string_literals(raw_json)
    if sanitized != raw_json:
        candidates.append(sanitized)
    balanced = _balance_json_brackets(raw_json)
    if balanced not in candidates:
        candidates.append(balanced)
    balanced_sanitized = _balance_json_brackets(sanitized)
    if balanced_sanitized not in candidates:
        candidates.append(balanced_sanitized)

    last_error: ValueError | None = None
    for candidate in candidates:
        try:
            return model.model_validate_json(candidate)
        except ValueError as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def parse_llm_json(content: str, model: Type[T]) -> T:
    """Extract and validate JSON from LLM response."""
    if not isinstance(content, str):
        raise ValueError(
            f"Could not parse JSON from LLM response: expected str, got {type(content).__name__}"
        )
    raw_json = _extract_json_object(content)
    return _parse_json_candidates(raw_json, model)


def llm_content(response) -> str:
    """Llm Content for this module."""
    raw = response.content if hasattr(response, "content") else response
    return _flatten_content_blocks(raw)


def invoke_tracked(llm, prompt: Any, *, agent: str):
    """Invoke LLM and record token usage when a pipeline tracker is active."""
    response = llm.invoke(prompt)
    record_llm_response(agent, response)
    return response
