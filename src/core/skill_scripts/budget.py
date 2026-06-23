#!/usr/bin/env python3
"""SCRIPT: budget.py — tool-call budget tracker for agent invocations."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

DEFAULT_BUDGET_DIR = Path(os.environ.get("AGENT_BUDGET_DIR", "/tmp"))


@dataclass
class BudgetState:
    agent: str
    thread_id: str
    hard_limit: int
    calls_made: int = 0

    @property
    def remaining(self) -> int:
        return max(0, self.hard_limit - self.calls_made)

    @property
    def abort(self) -> bool:
        return self.calls_made >= max(0, self.hard_limit - 2)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "thread_id": self.thread_id,
            "hard_limit": self.hard_limit,
            "calls_made": self.calls_made,
            "remaining": self.remaining,
            "abort": self.abort,
        }


def _budget_path(agent: str, thread_id: str, budget_dir: Path | None = None) -> Path:
    root = budget_dir or DEFAULT_BUDGET_DIR
    return root / f"agent_budget_{agent}_{thread_id}.json"


class BudgetTracker:
    def __init__(self, agent: str, thread_id: str, hard_limit: int, budget_dir: Path | None = None):
        self._path = _budget_path(agent, thread_id, budget_dir)
        self._state = BudgetState(agent=agent, thread_id=thread_id, hard_limit=hard_limit)

    def init(self) -> dict[str, Any]:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(self._state.to_dict(), indent=2), encoding="utf-8")
        return self._state.to_dict()

    def _load(self) -> BudgetState:
        if not self._path.exists():
            raise FileNotFoundError(f"Budget not initialized: {self._path}")
        data = json.loads(self._path.read_text(encoding="utf-8"))
        return BudgetState(
            agent=data["agent"],
            thread_id=data["thread_id"],
            hard_limit=data["hard_limit"],
            calls_made=data.get("calls_made", 0),
        )

    def check_and_decrement(self) -> dict[str, Any]:
        state = self._load()
        state.calls_made += 1
        self._path.write_text(json.dumps(state.to_dict(), indent=2), encoding="utf-8")
        return state.to_dict()


def init_budget(
    agent: str,
    thread_id: str,
    hard_limit: int,
    budget_dir: Path | None = None,
) -> dict[str, Any]:
    return BudgetTracker(agent, thread_id, hard_limit, budget_dir).init()


def check_and_decrement(
    agent: str,
    thread_id: str,
    hard_limit: int | None = None,
    budget_dir: Path | None = None,
) -> dict[str, Any]:
    tracker = BudgetTracker(agent, thread_id, hard_limit or 15, budget_dir)
    return tracker.check_and_decrement()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Agent tool-call budget tracker")
    sub = parser.add_subparsers(dest="command", required=True)

    init_p = sub.add_parser("init")
    init_p.add_argument("--agent", required=True)
    init_p.add_argument("--thread", required=True)
    init_p.add_argument("--limit", type=int, required=True)

    check_p = sub.add_parser("check")
    check_p.add_argument("--agent", required=True)
    check_p.add_argument("--thread", required=True)

    args = parser.parse_args(argv)
    if args.command == "init":
        result = init_budget(args.agent, args.thread, args.limit)
    else:
        path = _budget_path(args.agent, args.thread)
        if not path.exists():
            print(json.dumps({"error": "budget not initialized", "abort": True}))
            return 1
        data = json.loads(path.read_text(encoding="utf-8"))
        tracker = BudgetTracker(args.agent, args.thread, data["hard_limit"])
        result = tracker.check_and_decrement()
    print(json.dumps(result))
    return 0 if not result.get("abort") else 2


if __name__ == "__main__":
    sys.exit(main())
