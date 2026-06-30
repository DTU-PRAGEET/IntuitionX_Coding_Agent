"""Standalone Orchestrator Agent CLI — inspect Jira data and routing without the pipeline.

Examples:
    # Just fetch and print one Jira ticket (no LLM, no routing, no downstream agents)
    python -m src.agents.orchestrator --ticket PLAT-1234 --print-jira-only

    # Run the orchestrator routing decision (loads Jira, resolves repo, decides route)
    python -m src.agents.orchestrator --ticket PLAT-1234
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.agents.orchestrator.tools.jira import (
    JiraCredentialsError,
    credentials_configured,
    fetch_jira_ticket,
)
from src.core.config import settings
from src.core.logging import configure_logging, get_logger

_log = get_logger("orchestrator_cli")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run Orchestrator Agent standalone")
    parser.add_argument(
        "--ticket",
        default="PLAT-1234",
        help="Jira ticket id to fetch (only this ticket is loaded)",
    )
    parser.add_argument(
        "--print-jira-only",
        action="store_true",
        help="Fetch and print only the Jira ticket data; skip routing/LLM/downstream agents",
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Repository path for routing context (default: REPO_PATH or cwd)",
    )
    parser.add_argument("--prompt", default="", help="Optional user prompt")
    args = parser.parse_args(argv)

    if not credentials_configured():
        print(
            "[orchestrator_cli] FAILED: Jira credentials not configured — "
            "set JIRA_SERVER_URL, JIRA_EMAIL, JIRA_API_TOKEN",
            file=sys.stderr,
        )
        return 1

    try:
        ticket = fetch_jira_ticket(args.ticket)
    except JiraCredentialsError as e:
        print(f"[orchestrator_cli] FAILED: {e}", file=sys.stderr)
        return 1
    except Exception as e:
        _log.error("fetch_failed", ticket_id=args.ticket, error=str(e))
        print(f"[orchestrator_cli] FAILED: {e}", file=sys.stderr)
        return 1

    _log.info("ticket_source", source="jira", ticket_id=args.ticket)

    if args.print_jira_only:
        print(json.dumps(ticket, indent=2, default=str))
        return 0

    repo_path = str(Path(args.repo_path or settings.default_repo_path or ".").resolve())
    if not Path(repo_path).exists():
        _log.error("repo_missing", path=repo_path)
        print(f"[orchestrator_cli] FAILED: repo path not found: {repo_path}", file=sys.stderr)
        return 1

    from src.agents.orchestrator.agent import orchestrator_node
    from src.core.state import initial_state

    state = initial_state(args.ticket, user_prompt=args.prompt, repo_path=repo_path)

    try:
        out = orchestrator_node(state)
    except Exception as e:
        _log.error("cli_failed", error=str(e))
        print(f"[orchestrator_cli] FAILED: {e}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "ticket_id": args.ticket,
                "routing_route": out.get("routing_route"),
                "risk_level": out.get("risk_level"),
                "current_phase": out.get("current_phase"),
                "repo_path": out.get("repo_path"),
                "git_target_branch": out.get("git_target_branch"),
                "exit_message": out.get("exit_message"),
                "jira_ticket": out.get("jira_ticket"),
            },
            indent=2,
            default=str,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
