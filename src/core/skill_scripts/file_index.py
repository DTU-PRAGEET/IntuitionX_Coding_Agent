#!/usr/bin/env python3
"""
SCRIPT: file_index.py
PURPOSE: Build lightweight file manifest for research path grounding.
USAGE:
    python file_index.py --repo <repo_path> [--output /tmp/file_index.json]

STDOUT:
    JSON: {"total_files": N, "by_extension": {...}, "paths": [{"path", "size_bytes", "lines"}]}

EXIT CODES:
    0 = success
    1 = repo path invalid
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".gradle", "build", ".venv", "venv"}


def build_file_index(repo_path: str, *, max_files: int = 5000) -> dict:
    root = Path(repo_path)
    if not root.is_dir():
        raise ValueError(f"repo path is not a directory: {repo_path}")

    paths: list[dict] = []
    by_ext: Counter[str] = Counter()

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
        current_dir = Path(dirpath)
        for name in filenames:
            if name.startswith("."):
                continue
            full = current_dir / name
            try:
                rel = full.relative_to(root).as_posix()
                stat = full.stat()
                try:
                    text = full.read_text(encoding="utf-8", errors="replace")
                    lines = len(text.splitlines())
                except (OSError, UnicodeDecodeError):
                    lines = 0
                ext = full.suffix.lower() or "(none)"
                by_ext[ext] += 1
                paths.append(
                    {
                        "path": rel,
                        "size_bytes": stat.st_size,
                        "lines": lines,
                    }
                )
                if len(paths) >= max_files:
                    break
            except (OSError, ValueError):
                continue
        if len(paths) >= max_files:
            break

    paths.sort(key=lambda p: p["path"])
    return {
        "total_files": len(paths),
        "by_extension": dict(by_ext),
        "paths": paths,
        "truncated": len(paths) >= max_files,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build repository file index")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--output", default=None, help="Optional output JSON path")
    args = parser.parse_args(argv)

    try:
        index = build_file_index(args.repo)
    except ValueError as e:
        print(json.dumps({"error": str(e)}))
        return 1

    payload = json.dumps(index, indent=2)
    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    sys.exit(main())
