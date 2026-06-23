#!/usr/bin/env python3
"""Assemble orchestrator context JSON from ticket fields."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


def build_context(ticket: dict, extra: dict | None = None) -> dict:
    ctx = {"jira_ticket": ticket}
    if extra:
        ctx.update(extra)
    return ctx


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build orchestrator context JSON")
    parser.add_argument("--ticket-json", required=True, help="Path to ticket JSON file")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    ticket = json.loads(Path(args.ticket_json).read_text(encoding="utf-8"))
    ctx = build_context(ticket)
    payload = json.dumps(ctx, indent=2, default=str)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
