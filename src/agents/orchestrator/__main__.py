"""Standalone Orchestrator Agent CLI — inspect Jira data and routing without the pipeline.

Examples:
    # Just fetch and print one Jira ticket (no LLM, no routing, no downstream agents)
    python -m src.agents.orchestrator --ticket SEC-1234 --print-jira-only

    # Same, but use the built-in mock SEC-4421 security ticket
    python -m src.agents.orchestrator --ticket SEC-4421 --mock-security --print-jira-only

    # Run the orchestrator routing decision (loads Jira, resolves repo, decides route)
    python -m src.agents.orchestrator --ticket SEC-4421 --mock-security
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.agents.orchestrator.tools.jira import (
    credentials_configured,
    fetch_jira_ticket,
    mock_security_jira_ticket,
)
from src.core.config import settings
from src.core.logging import configure_logging, get_logger

_log = get_logger("orchestrator_cli")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run Orchestrator Agent standalone")
    parser.add_argument(
        "--ticket",
        default="SEC-4421",
        help="Jira ticket id to fetch (only this ticket is loaded)",
    )
    parser.add_argument(
        "--mock-security",
        action="store_true",
        help="Use the built-in mock SQL-injection security ticket instead of real Jira",
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

    if args.mock_security:
        ticket = mock_security_jira_ticket(args.ticket)
        _log.info("ticket_source", source="mock_security", ticket_id=args.ticket)
    else:
        if not credentials_configured():
            _log.warning(
                "jira_credentials_missing",
                ticket_id=args.ticket,
                note="falling back to generic mock ticket; set JIRA_* env or use --mock-security",
            )
        ticket = fetch_jira_ticket(args.ticket)
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

    user_prompt = args.prompt
    if args.mock_security and "[mock-security]" not in user_prompt:
        user_prompt = f"[mock-security] {user_prompt}".strip()

    state = initial_state(args.ticket, user_prompt=user_prompt, repo_path=repo_path)

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
