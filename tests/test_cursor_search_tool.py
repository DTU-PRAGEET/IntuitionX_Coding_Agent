"""Tests for cursor_search LangChain tool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.agents.research.tools.cursor_search import make_cursor_search_tool
from src.core.skill_scripts.budget import init_budget
from src.integrations.cursor.gateway import DryRunCursorGateway


@pytest.fixture
def gateway(tmp_path):
    return DryRunCursorGateway(tmp_path)


@pytest.fixture
def search_tool(gateway, tmp_path):
    init_budget("research", "test-thread", hard_limit=3, budget_dir=tmp_path)
    return make_cursor_search_tool(
        gateway,
        thread_id="test-thread",
        hard_limit=3,
        memory=None,
        budget_dir=tmp_path,
    )


def test_cursor_search_returns_structured_json(search_tool):
    result = search_tool.invoke({"query": "sql injection in user search"})
    payload = json.loads(result)
    assert payload["hits"]
    assert payload["hits"][0]["path"].endswith("UserSearchController.py")


def test_cursor_search_budget_abort(gateway, tmp_path):
    init_budget("research", "abort-thread", hard_limit=1, budget_dir=tmp_path)
    tool = make_cursor_search_tool(
        gateway,
        thread_id="abort-thread",
        hard_limit=1,
        memory=None,
        budget_dir=tmp_path,
    )
    tool.invoke({"query": "first"})
    result = tool.invoke({"query": "second should abort"})
    payload = json.loads(result)
    assert payload.get("abort") is True


def test_cursor_search_records_on_search_callback(gateway, tmp_path):
    init_budget("research", "cb-thread", hard_limit=5)
    seen = []

    tool = make_cursor_search_tool(
        gateway,
        thread_id="cb-thread",
        hard_limit=5,
        on_search=lambda entry: seen.append(entry),
    )
    tool.invoke({"query": "authentication middleware"})
    assert len(seen) == 1
    assert seen[0]["query"] == "authentication middleware"
    assert seen[0]["method"] == "search"
