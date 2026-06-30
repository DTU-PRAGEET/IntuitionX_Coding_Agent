"""Web search tool for Research Agent."""

from langchain_core.tools import tool


@tool
def web_search(query: str) -> str:
    """Web search for CVE/docs references. Args: query."""
    try:
        from duckduckgo_search import DDGS

        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=5))
        if not results:
            return "No results found."
        return "\n".join(
            f"- {r.get('title', '')}: {r.get('body', '')[:200]}" for r in results
        )
    except Exception as e:
        return f"Web search unavailable: {e}"
