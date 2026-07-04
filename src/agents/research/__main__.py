"""Standalone Research Agent CLI — verify Level 2 without Orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.core.skill_manager import default_skills_root

from src.agents.orchestrator.tools.jira import JiraCredentialsError, credentials_configured, fetch_jira_ticket
from src.agents.research.deep_runner import run_research
from src.agents.research.memory import InMemoryResearchMemory
from src.agents.research.tools.repo_profile import profile_repository
from src.core.config import settings
from src.core.logging import configure_logging, get_logger

_log = get_logger("research_cli")


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for this module."""
    configure_logging()
    parser = argparse.ArgumentParser(description="Run Research Deep Agent standalone")
    parser.add_argument("--ticket", default="PLAT-1234", help="Jira ticket id")
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Repository path to investigate (default: REPO_PATH or cwd)",
    )
    parser.add_argument("--prompt", default="", help="Optional user prompt")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Build context and tools only; skip LLM invocation",
    )
    args = parser.parse_args(argv)

    repo_path = str(Path(args.repo_path or settings.default_repo_path or ".").resolve())
    if not Path(repo_path).exists():
        _log.error("repo_missing", path=repo_path)
        return 1

    if not credentials_configured():
        print(
            "[research_cli] FAILED: Jira credentials not configured — "
            "set JIRA_SERVER_URL, JIRA_EMAIL, JIRA_API_TOKEN",
            file=sys.stderr,
        )
        return 1

    try:
        ticket = fetch_jira_ticket(args.ticket)
    except JiraCredentialsError as e:
        print(f"[research_cli] FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        _log.error("fetch_failed", ticket_id=args.ticket, error=str(e))
        print(f"[research_cli] FAILED: {e}", file=sys.stderr)
        return 1

    _log.info("ticket_source", source="jira", ticket_id=args.ticket)

    profile = profile_repository(repo_path)
    memory = InMemoryResearchMemory()

    if args.dry_run:
        from src.agents.research.deep_runner import build_research_prompt, resolve_research_skill
        from src.agents.research.tools.file_discovery import extract_anchors
        from src.agents.research.tools.builder import build_research_tool_registry
        from src.integrations.cursor.gateway import get_cursor_gateway

        anchors = extract_anchors(ticket, args.prompt)
        loaded_skill, skill_reason = resolve_research_skill(ticket)
        prompt = build_research_prompt(
            ticket, repo_path, profile, anchors, args.prompt, memory
        )
        gateway = get_cursor_gateway(repo_path, dry_run=True)
        _, tools = build_research_tool_registry(
            repo_path, gateway=gateway, thread_id=ticket.get("issue_id") or "research", memory=memory
        )
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "ticket_id": ticket.get("issue_id"),
                    "repo_path": repo_path,
                    "tool_names": [t.name for t in tools],
                    "anchors": anchors,
                    "prompt_chars": len(prompt),
                    "skills_mode": "deepagents_progressive",
                    "skills_root": str(default_skills_root()),
                    "loaded_skill": loaded_skill.name if loaded_skill else None,
                    "skill_reason": skill_reason,
                },
                indent=2,
            )
        )
        return 0

    try:
        report = run_research(
            ticket=ticket,
            repo_path=repo_path,
            user_prompt=args.prompt,
            repo_profile=profile,
            memory=memory,
        )
        print(json.dumps(report.model_dump(), indent=2))
        print("\n--- run memory ---")
        print(memory.to_context_snippet())
        return 0
    except Exception as e:
        _log.error("cli_failed", error=str(e))
        print(f"[research_cli] FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
