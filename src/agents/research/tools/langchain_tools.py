"""LangChain tool wrappers for Research agent-specific capabilities."""

from __future__ import annotations

import json

from langchain_core.tools import tool

from src.agents.research.tools.repo_profile import profile_repository


def make_repo_profile_tool(repo_path: str):
    """Factory — bind repo path for repo_profile tool."""

    @tool
    def repo_profile() -> str:
        """Profile repository layout (Java/Gradle vs Python, modules, package roots)."""
        profile = profile_repository(repo_path)
        return json.dumps(dict(profile), indent=2)

    return repo_profile
