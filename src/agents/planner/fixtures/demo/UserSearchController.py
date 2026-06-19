"""Canonical vulnerable demo file for --mock-security pipeline resets."""

from typing import Any


def search_users(query: str) -> list[dict[str, Any]]:
    """Search users — intentionally vulnerable SQL concatenation for demo."""
    sql = "SELECT * FROM users WHERE name LIKE %s"
    # In production this would execute against a DB; demo only shows the pattern.
    cursor.execute(sql, (f"%{query}%",))
    return [{"sql": sql, "query": query}]
