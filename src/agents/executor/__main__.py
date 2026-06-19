"""Standalone Executor Agent CLI — verify Level 4b without Orchestrator."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from src.agents.executor.agent import load_execution_plan, run_executor
from src.core.config import settings
from src.core.logging import configure_logging, get_logger

_log = get_logger("executor_cli")


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Run Executor Agent standalone")
    parser.add_argument(
        "--plan",
        required=True,
        help="Path to ExecutionPlan JSON fixture",
    )
    parser.add_argument(
        "--repo-path",
        default=None,
        help="Repository/workspace path to apply changes (default: REPO_PATH or cwd)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate plan JSON only; do not execute steps",
    )
    args = parser.parse_args(argv)

    plan_path = Path(args.plan)
    if not plan_path.is_file():
        _log.error("plan_missing", path=str(plan_path))
        print(f"[executor_cli] FAILED: plan file not found: {plan_path}", file=sys.stderr)
        return 1

    repo_path = str(Path(args.repo_path or settings.default_repo_path or ".").resolve())
    repo = Path(repo_path)
    if not repo.exists():
        _log.error("repo_missing", path=repo_path)
        print(f"[executor_cli] FAILED: repo path not found: {repo_path}", file=sys.stderr)
        return 1

    try:
        plan = load_execution_plan(plan_path)
    except (ValidationError, ValueError) as e:
        _log.error("plan_invalid", error=str(e))
        print(f"[executor_cli] FAILED: {e}", file=sys.stderr)
        return 1

    _log.info("plan_loaded", ticket_id=plan.ticket_id, path=str(plan_path), steps=len(plan.execution_steps))

    if args.dry_run:
        print(
            json.dumps(
                {
                    "dry_run": True,
                    "ticket_id": plan.ticket_id,
                    "repo_path": repo_path,
                    "steps": len(plan.execution_steps),
                    "files_to_change": plan.files_to_change,
                },
                indent=2,
            )
        )
        return 0

    try:
        result = run_executor(plan, repo_path)
        print(json.dumps(result.model_dump(), indent=2))
        return 0
    except Exception as e:
        _log.error("cli_failed", error=str(e))
        print(f"[executor_cli] FAILED: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
