"""Standalone Planner Agent CLI — verify Level 4a without Orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from src.agents.planner.agent import build_planner_prompt, load_research_report, run_planner
from src.core.config import settings
from src.core.logging import configure_logging, get_logger

_log = get_logger("planner_cli")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run Planner Agent standalone")
    parser.add_argument(
        "--report",
        required=True,
        help="Path to ResearchReport JSON fixture",
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Repository path for verification tools (default: REPO_PATH or cwd)",
    )
    parser.add_argument(
        "--routing-route",
        default="FULL",
        choices=["FULL", "SKIP_RESEARCH", "MICRO"],
        help="Routing context for plan scope",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate report and build prompt only; skip LLM",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="Skip bounded read/grep/find verification after planning",
    )
    args = parser.parse_args(argv)

    report_path = Path(args.report)
    if not report_path.is_file():
        _log.error("report_missing", path=str(report_path))
        print(f"[planner_cli] FAILED: report file not found: {report_path}", file=sys.stderr)
        return 1

    repo_path = str(Path(args.repo_path or settings.default_repo_path or ".").resolve())
    if not Path(repo_path).exists():
        _log.error("repo_missing", path=repo_path)
        print(f"[planner_cli] FAILED: repo path not found: {repo_path}", file=sys.stderr)
        return 1

    try:
        report = load_research_report(report_path)
    except (ValidationError, ValueError) as e:
        _log.error("report_invalid", error=str(e))
        print(f"[planner_cli] FAILED: {e}", file=sys.stderr)
        return 1

    _log.info("report_loaded", ticket_id=report.ticket_id, path=str(report_path))

    if args.dry_run:
        prompt = build_planner_prompt(report, repo_path, routing_route=args.routing_route)
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "ticket_id": report.ticket_id,
                    "repo_path": repo_path,
                    "routing_route": args.routing_route,
                    "prompt_chars": len(prompt),
                    "affected_files": [f.path for f in report.affected_files],
                },
                indent=2,
            )
        )
        return 0

    try:
        plan = run_planner(
            report,
            repo_path,
            routing_route=args.routing_route,
            verify=not args.no_verify,
        )
        print(json.dumps(plan.model_dump(), indent=2))
        return 0
    except Exception as e:
        _log.error("cli_failed", error=str(e))
        print(f"[planner_cli] FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
