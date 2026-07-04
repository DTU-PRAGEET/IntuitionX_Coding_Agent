"""Tests for Cursor SDK bridge auth-token workaround."""

from __future__ import annotations

from src.integrations.cursor.client import _ensure_bridge_safe_auth_tokens


def test_bridge_auth_tokens_never_start_with_dash():
    """cursor-sdk-bridge rejects argv values that look like flags."""
    _ensure_bridge_safe_auth_tokens()
    from cursor_sdk import _store_callback as store_cb
    from cursor_sdk import _tool_callback as tool_cb

    for _ in range(200):
        assert not tool_cb._new_auth_token().startswith("-")
        assert not store_cb._new_auth_token().startswith("-")


def test_bridge_argv_accepts_patched_tool_callback_token():
    """Simulate bridge takeValue() — dash-prefixed tokens must not be emitted."""
    _ensure_bridge_safe_auth_tokens()
    from cursor_sdk._tool_callback import ToolCallbackServer, tool_callback_bridge_argv

    server = ToolCallbackServer()
    try:
        argv = tool_callback_bridge_argv(server.endpoint)
        token = argv[argv.index("--tool-callback-auth-token") + 1]
        assert not token.startswith("-")
    finally:
        server.close()
