"""Canonical vulnerable demo file for --mock-security pipeline resets."""

from typing import Any


def search_users(query: str) -> list[dict[str, Any]]:
    """Search users — intentionally vulnerable SQL concatenation for demo."""
    sql = f"SELECT * FROM users WHERE name LIKE '%{query}%'"
    # In production this would execute against a DB; demo only shows the pattern.
    return [{"sql": sql, "query": query}]
