#!/usr/bin/env python3
"""
SCRIPT: grep_symbols.py
PURPOSE: Targeted symbol/keyword search across repository.
USAGE:
    python grep_symbols.py --repo <repo_path> --patterns kw1,kw2 [--output /tmp/symbol_hits.json]

STDOUT:
    JSON: {"patterns": [...], "hits": [{"path", "line", "text", "pattern"}]}

EXIT CODES:
    0 = success
    1 = validation error
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from src.tools.registry import ToolRegistry

DEFAULT_LIMIT = 50


def grep_symbols(
    repo_path: str,
    patterns: list[str],
    *,
    limit_per_pattern: int = DEFAULT_LIMIT,
) -> dict:
    registry = ToolRegistry(repo_path)
    hits: list[dict] = []
    try:
        for pattern in patterns:
            pattern = pattern.strip()
            if not pattern:
                continue
            result = registry.execute("grep", pattern=re.escape(pattern), path=".", limit=limit_per_pattern)
            if result.is_error:
                continue
            for line in result.output.splitlines():
                if line == "No matches" or line.startswith("[Truncated"):
                    continue
                match = re.match(r"^([^:]+):(\d+):\s*(.*)$", line)
                if match:
                    hits.append(
                        {
                            "path": match.group(1),
                            "line": int(match.group(2)),
                            "text": match.group(3),
                            "pattern": pattern,
                        }
                    )
    finally:
        registry.dispose()

    by_path: dict[str, int] = {}
    for h in hits:
        by_path[h["path"]] = by_path.get(h["path"], 0) + 1

    return {
        "patterns": patterns,
        "total_hits": len(hits),
        "hits": hits,
        "files_by_hit_count": dict(sorted(by_path.items(), key=lambda x: -x[1])),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Search repository for symbol patterns")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--patterns", required=True, help="Comma-separated keywords")
    parser.add_argument("--output", default=None)
    args = parser.parse_args(argv)

    patterns = [p.strip() for p in args.patterns.split(",") if p.strip()]
    if not patterns:
        print(json.dumps({"error": "no patterns provided"}))
        return 1

    data = grep_symbols(args.repo, patterns)
    payload = json.dumps(data, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
