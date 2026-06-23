#!/usr/bin/env python3
"""
SCRIPT: run_tests.py
PURPOSE: Run relevant pytest subset for a changed file.
USAGE:
    python run_tests.py --repo <repo_path> --file <path/to/file.py>

STDOUT (on success):
    TEST_OK: N passed

STDOUT (on failure):
    TEST_FAIL: <pytest output snippet>

EXIT CODES:
    0 = all tests passed
    1 = tests failed or pytest error
    2 = no tests found (skipped)
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path


def _guess_test_path(repo_path: str, file_path: str) -> list[str]:
    root = Path(repo_path)
    rel = Path(file_path)
    candidates: list[Path] = []

    stem = rel.stem
    if stem.startswith("test_"):
        candidates.append(rel)
    else:
        candidates.append(Path("tests") / f"test_{stem}.py")
        candidates.append(Path("tests") / rel.parent / f"test_{stem}.py")

    existing = [str(root / c) for c in candidates if (root / c).is_file()]
    if existing:
        return existing
    return ["tests/"] if (root / "tests").is_dir() else []


def run_tests_for_file(repo_path: str, file_path: str, *, timeout: int = 120) -> tuple[bool, str, int]:
    targets = _guess_test_path(repo_path, file_path)
    if not targets:
        return True, f"TEST_SKIP: no tests found for {file_path}", 2

    cmd = [sys.executable, "-m", "pytest", *targets, "-q", "--tb=short"]
    try:
        result = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return False, "TEST_FAIL: pytest timed out", 1
    except FileNotFoundError:
        return False, "TEST_FAIL: pytest not available", 1

    combined = (result.stdout or "") + (result.stderr or "")
    snippet = combined.strip()[-2000:]
    if result.returncode == 0:
        return True, f"TEST_OK: passed\n{snippet}", 0
    return False, f"TEST_FAIL:\n{snippet}", 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run pytest subset for a file")
    parser.add_argument("--repo", required=True)
    parser.add_argument("--file", required=True)
    args = parser.parse_args(argv)

    ok, out, code = run_tests_for_file(args.repo, args.file)
    print(out)
    if code == 2:
        return 0
    return code if not ok else 0


if __name__ == "__main__":
    sys.exit(main())
