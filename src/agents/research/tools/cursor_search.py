"""LangChain tool — natural-language repository search via CursorGateway."""

from __future__ import annotations

import json
from typing import Callable

from pathlib import Path

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from src.agents.research.memory import ResearchMemoryStore
from src.core.logging import get_logger
from src.core.skill_scripts.budget import check_and_decrement
from src.integrations.cursor.gateway import CursorGateway

_log = get_logger("cursor_search_tool")


class CursorSearchInput(BaseModel):
    query: str = Field(
        description=(
            "Natural-language search question, e.g. "
            "'Find authentication middleware' or 'Where are JWT tokens validated?'"
        )
    )
    path: str = Field(
        default=".",
        description="Optional subdirectory to scope the search (relative to repo root)",
    )


def make_cursor_search_tool(
    gateway: CursorGateway,
    *,
    agent: str = "research",
    thread_id: str = "default",
    hard_limit: int = 6,
    memory: ResearchMemoryStore | None = None,
    on_search: Callable[[dict], None] | None = None,
    budget_dir: Path | None = None,
) -> StructuredTool:
    """Factory — bind gateway and budget for cursor_search LangChain tool."""

    def cursor_search(query: str, path: str = ".") -> str:
        from src.core.skill_scripts.budget import _budget_path

        budget_path = _budget_path(agent, thread_id, budget_dir)
        if budget_path.exists():
            existing = json.loads(budget_path.read_text(encoding="utf-8"))
            if existing.get("calls_made", 0) >= hard_limit:
                msg = (
                    f"cursor_search budget exhausted ({existing.get('calls_made')}/"
                    f"{hard_limit}). Emit ResearchReport with partial evidence."
                )
                _log.warning("cursor_search_budget_abort", **existing)
                if memory:
                    memory.record("cursor_search", {"query": query, "abort": True, **existing})
                return json.dumps({"error": msg, "abort": True, **existing})

        budget = check_and_decrement(agent, thread_id, hard_limit, budget_dir)
        _log.info("cursor_search", query=query[:120], path=path, remaining=budget.get("remaining"))
        result = gateway.search(query, path=path)
        response = result.search_response
        if response is None:
            from src.integrations.cursor.gateway import parse_search_response

            response = parse_search_response(result.text, query)

        payload = response.model_dump()
        if memory:
            memory.record(
                "cursor_search",
                {
                    "query": query,
                    "path": path,
                    "hit_count": len(response.hits),
                    "flags": response.flags,
                    "dry_run": result.dry_run,
                },
            )
        if on_search:
            on_search({"query": query, "path": path, "response": payload, "method": "search"})

        return json.dumps(payload, indent=2)

    return StructuredTool.from_function(
        func=cursor_search,
        name="cursor_search",
        description=(
            "Search the repository using natural language. Cursor navigates the codebase "
            "and returns structured hits (file paths, confidence, summaries, snippets). "
            "Use for locating code — do NOT use read/grep/find directly."
        ),
        args_schema=CursorSearchInput,
    )
