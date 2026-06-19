"""Shared utilities for thin agent runners."""

from __future__ import annotations

import json
import re
from typing import Type, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


def parse_llm_json(content: str, model: Type[T]) -> T:
    """Extract and validate JSON from LLM response."""
    json_match = re.search(r"\{.*\}", content, re.DOTALL)
    if not json_match:
        raise ValueError(f"Could not parse JSON from LLM response: {content[:200]}")
    return model.model_validate_json(json_match.group())


def llm_content(response) -> str:
    return response.content if hasattr(response, "content") else str(response)
