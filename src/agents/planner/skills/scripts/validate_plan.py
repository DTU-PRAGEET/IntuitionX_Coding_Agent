#!/usr/bin/env python3
"""
SCRIPT: validate_plan.py
PURPOSE: Validate execution plan paths and READ→EDIT→VERIFY triad ordering.
USAGE:
    python validate_plan.py --research <research.json> --plan <plan.json> --repo <repo_path>

STDOUT:
    VALID_OK or VALID_FAIL: <reason>

EXIT CODES:
    0 = valid
    1 = validation failed
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from src.core.plan_validation import validate_plan_paths, validate_plan_triad
from src.core.schemas import ExecutionPlan, ResearchReport


def validate_plan_triad(plan: ExecutionPlan) -> list[str]:
    from src.core.plan_validation import validate_plan_triad as _validate

    return _validate(plan)


def validate_plan_paths(plan: ExecutionPlan, research: ResearchReport, repo: Path) -> list[str]:
    from src.core.plan_validation import validate_plan_paths as _validate_paths

    return _validate_paths(plan, research, str(repo))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate execution plan")
    parser.add_argument("--research", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--repo", required=True)
    args = parser.parse_args(argv)

    research = ResearchReport.model_validate(json.loads(Path(args.research).read_text()))
    plan = ExecutionPlan.model_validate(json.loads(Path(args.plan).read_text()))
    repo = Path(args.repo)

    errors = validate_plan_triad(plan) + validate_plan_paths(plan, research, repo)
    if errors:
        print("VALID_FAIL: " + "; ".join(errors))
        return 1
    print("VALID_OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
