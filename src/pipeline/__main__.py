"""End-to-end pipeline CLI — Research → Planner → Executor."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.core.config import settings
from src.core.logging import configure_logging, get_logger
from src.pipeline.runner import PipelineError, run_pipeline

_log = get_logger("pipeline_cli")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run full Jira solver pipeline")
    parser.add_argument("--ticket", default="SEC-4421", help="Jira ticket id")
    parser.add_argument(
        "--mock-security",
        action="store_true",
        help="Use built-in sample security ticket",
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Repository path (default: REPO_PATH or cwd)",
    )
    parser.add_argument("--prompt", default="", help="Optional user prompt")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Log node transitions only; skip LLM and execution",
    )
    parser.add_argument(
        "--with-gates",
        action="store_true",
        help="Enable HITL gate logging (auto-approved in CLI demo)",
    )
    args = parser.parse_args(argv)

    repo_path = str(Path(args.repo_path or settings.default_repo_path or ".").resolve())
    if not Path(repo_path).exists():
        _log.error("repo_missing", path=repo_path)
        print(f"[pipeline_cli] FAILED: repo path not found: {repo_path}", file=sys.stderr)
        return 1

    try:
        result = run_pipeline(
            args.ticket,
            repo_path,
            mock_security=args.mock_security,
            user_prompt=args.prompt,
            dry_run=args.dry_run,
            with_gates=args.with_gates,
        )
    except PipelineError as e:
        print(str(e), file=sys.stderr)
        return 1
    except Exception as e:
        _log.error("cli_failed", error=str(e))
        print(f"[pipeline_cli] FAILED: {e}", file=sys.stderr)
        return 1

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "ticket_id": result.ticket_id,
                    "repo_path": result.repo_path,
                    "routing_route": result.routing_route,
                },
                indent=2,
            )
        )
        return 0

    if not result.success:
        print(f"[pipeline_cli] EXIT: {result.error}", file=sys.stderr)
        return 1

    print(
        json.dumps(
            {
                "success": True,
                "ticket_id": result.ticket_id,
                "routing_route": result.routing_route,
                "research_ticket_id": (result.research_output or {}).get("ticket_id"),
                "plan_steps": len(result.plan.execution_steps) if result.plan else 0,
                "executor_steps": result.execution.steps_completed if result.execution else 0,
                "changed_files": result.execution.changed_files if result.execution else [],
            },
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
