"""Cursor SDK proof-of-concept — headless programmatic file editing (milestone 1.1)."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from src.core.logging import configure_logging, get_logger

_log = get_logger("cursor_poc")

DEFAULT_PROMPT = (
    "Read the repository structure and list the top-level directories. "
    "Do not modify any files."
)


def _git_diff(cwd: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "diff", "--stat"],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        return result.stdout.strip() or "(no diff)"
    except (OSError, subprocess.TimeoutExpired):
        return "(git diff unavailable)"


def run_poc(*, cwd: Path, prompt: str, dry_run: bool) -> int:
    api_key = os.environ.get("CURSOR_API_KEY", "").strip()
    if dry_run or not api_key:
        _log.info(
            "cursor_poc_dry_run",
            cwd=str(cwd),
            has_api_key=bool(api_key),
            prompt_preview=prompt[:120],
        )
        print(f"[dry-run] cwd={cwd}")
        print(f"[dry-run] prompt={prompt[:200]}")
        print(f"[dry-run] git_diff_preview={_git_diff(cwd)}")
        return 0

    try:
        from cursor_sdk import Agent, AgentOptions, LocalAgentOptions
    except ImportError:
        _log.error("cursor_sdk_missing", hint="pip install cursor-sdk")
        print("cursor-sdk not installed. Run: pip install cursor-sdk", file=sys.stderr)
        return 1

    _log.info("cursor_poc_start", cwd=str(cwd))
    result = Agent.prompt(
        prompt,
        AgentOptions(
            api_key=api_key,
            model="composer-2.5",
            local=LocalAgentOptions(cwd=str(cwd.resolve())),
        ),
    )
    diff = _git_diff(cwd)
    _log.info("cursor_poc_complete", status=result.status, diff_lines=len(diff.splitlines()))
    print(f"status={result.status}")
    print(f"result_preview={(result.result or '')[:500]}")
    print(f"git_diff:\n{diff}")
    return 0 if result.status == "finished" else 2


def main(argv: list[str] | None = None) -> int:
    configure_logging()
    parser = argparse.ArgumentParser(description="Cursor SDK headless edit POC")
    parser.add_argument("--cwd", default=".", help="Working directory for local agent")
    parser.add_argument("--prompt", default=DEFAULT_PROMPT, help="Agent prompt")
    parser.add_argument("--dry-run", action="store_true", help="Skip SDK call; validate wiring")
    args = parser.parse_args(argv)
    cwd = Path(args.cwd).resolve()
    if not cwd.is_dir():
        print(f"Invalid cwd: {cwd}", file=sys.stderr)
        return 1
    return run_poc(cwd=cwd, prompt=args.prompt, dry_run=args.dry_run)


if __name__ == "__main__":
    raise SystemExit(main())
