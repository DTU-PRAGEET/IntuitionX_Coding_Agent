"""Shared utilities for thin agent runners."""

from __future__ import annotations

import re
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


def parse_llm_json(content: str, model: Type[T]) -> T:
    """Extract and validate JSON from LLM response."""
    if not isinstance(content, str):
        raise ValueError(
            f"Could not parse JSON from LLM response: expected str, got {type(content).__name__}"
        )
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if not json_match:
        raise ValueError(f"Could not parse JSON from LLM response: {content[:200]}")
    return model.model_validate_json(json_match.group())


def llm_content(response) -> str:
    """Llm Content for this module."""
    raw = response.content if hasattr(response, "content") else response
    return _flatten_content_blocks(raw)


def invoke_tracked(llm, prompt: Any, *, agent: str):
    """Invoke LLM and record token usage when a pipeline tracker is active."""
    response = llm.invoke(prompt)
    record_llm_response(agent, response)
    return response
