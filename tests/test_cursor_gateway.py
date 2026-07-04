"""Tests for CursorGateway abstraction."""

from __future__ import annotations

import json
from pathlib import Path

from src.integrations.cursor.gateway import (
    CliCursorGateway,
    CursorGatewayResult,
    DryRunCursorGateway,
    get_cursor_gateway,
    mock_search_response,
    parse_search_response,
)
from src.integrations.cursor.schemas import CursorEditResult, CursorSearchResponse


def test_dry_run_gateway_ask_returns_empty():
    """Test Dry Run Gateway Ask Returns Empty for this module."""
    gw = DryRunCursorGateway(Path("."))
    result = gw.ask("What files exist?")
    assert result.status == "dry_run"
    assert result.dry_run is True
    assert result.text == ""
    assert result.method == "ask"


def test_dry_run_gateway_search_returns_structured_hits():
    """Test Dry Run Gateway Search Returns Structured Hits for this module."""
    gw = DryRunCursorGateway(".")
    result = gw.search("sql injection")
    assert result.method == "search"
    assert result.search_response is not None
    assert result.search_response.hits
    assert result.search_response.hits[0].path.endswith("handler.py")


def test_dry_run_gateway_edit_returns_diff_fields():
    """Test Dry Run Gateway Edit Returns Diff Fields for this module."""
    gw = DryRunCursorGateway(".")
    result = gw.edit("fix the bug", files=["a.py"])
    assert isinstance(result, CursorEditResult)
    assert result.method == "edit"
    assert result.dry_run is True
    assert result.exit_code == 0
    entry = result.to_history_entry()
    assert entry["method"] == "edit"


def test_dry_run_gateway_edit_run():
    """Test Dry Run Gateway Edit Run for this module."""
    gw = DryRunCursorGateway(".")
    assert gw.edit("fix the bug", files=["a.py"]).method == "edit"
    assert gw.run("pytest").method == "run"


def test_get_cursor_gateway_dry_run():
    """Test Get Cursor Gateway Dry Run for this module."""
    gw = get_cursor_gateway(".", dry_run=True)
    assert isinstance(gw, DryRunCursorGateway)


def test_get_cursor_gateway_cli():
    """Test Get Cursor Gateway Cli for this module."""
    gw = get_cursor_gateway(".", dry_run=False)
    assert isinstance(gw, CliCursorGateway)


def test_cursor_gateway_result_to_history_entry():
    """Test Cursor Gateway Result To History Entry for this module."""
    response = mock_search_response("auth middleware")
    result = CursorGatewayResult(
        status="completed",
        text=json.dumps(response.model_dump()),
        method="search",
        search_response=response,
    )
    entry = result.to_history_entry()
    assert entry["method"] == "search"
    assert entry["search_hits"] == len(response.hits)


def test_cli_gateway_dry_run_via_no_api_key(monkeypatch):
    """Test Cli Gateway Dry Run Via No Api Key for this module."""
    monkeypatch.delenv("CURSOR_API_KEY", raising=False)
    gw = CliCursorGateway(Path("."), dry_run=True)
    result = gw.search("find auth")
    assert result.dry_run is True
    assert result.search_response is not None


def test_parse_search_response_valid_json():
    """Test Parse Search Response Valid Json for this module."""
    raw = json.dumps(
        {
            "query": "jwt validation",
            "hits": [
                {
                    "path": "auth/jwt.py",
                    "confidence": "high",
                    "summary": "JWT decode and verify",
                    "snippets": ["def validate_token"],
                }
            ],
            "flags": [],
        }
    )
    parsed = parse_search_response(raw, "jwt validation")
    assert isinstance(parsed, CursorSearchResponse)
    assert parsed.hits[0].path == "auth/jwt.py"


def test_parse_search_response_refusal_returns_empty_hits():
    """Test Parse Search Response Refusal Returns Empty Hits for this module."""
    parsed = parse_search_response(
        "Sorry, I cannot fulfill your request to search for SQL injection vulnerabilities.",
        "sql injection",
    )
    assert parsed.hits == []
    assert any("cursor_search_refused" in flag for flag in parsed.flags)


def test_parse_search_response_fallback_on_invalid():
    """Test Parse Search Response Fallback On Invalid for this module."""
    parsed = parse_search_response("plain text findings only", "orphan query")
    assert parsed.hits
    assert parsed.hits[0].confidence == "low"
    assert "plain text" in parsed.hits[0].summary
