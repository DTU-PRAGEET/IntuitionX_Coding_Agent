"""Grep tool — ripgrep with Python fallback."""

from __future__ import annotations

import glob as glob_module
import os
import re
import shutil
import subprocess

from src.core.schemas import GrepInput, ToolResult
from src.tools.base import ToolDefinition, audit_tool
from src.tools.truncate import GREP_MAX_LINE_LENGTH, truncate_line

DEFAULT_LIMIT = 100
SKIP_DIRS = (".git", "node_modules", "__pycache__", ".gradle", "/build/")


class GrepTool(ToolDefinition):
    """Search for regex pattern in files under workspace path."""

    name = "grep"
    input_schema = GrepInput

    @audit_tool
    def execute(
        self,
        pattern: str,
        path: str = ".",
        glob_pattern: str = "**/*",
        limit: int = DEFAULT_LIMIT,
    ) -> ToolResult:
        try:
            root = self.resolve_path(path)
        except ValueError as e:
            return self._wrap_output(str(e), 1, True)

        if shutil.which("rg") and root.is_dir():
            return self._grep_ripgrep(pattern, root, glob_pattern, limit, path)
        if root.is_file():
            return self._grep_file(pattern, path, root, limit)
        return self._grep_python(pattern, root, glob_pattern, limit)

    def _grep_ripgrep(
        self, pattern: str, root: os.PathLike, glob_pattern: str, limit: int, display_path: str
    ) -> ToolResult:
        cmd = ["rg", "--line-number", "--no-heading", "-e", pattern, str(root)]
        if glob_pattern and glob_pattern != "**/*":
            cmd.extend(["--glob", glob_pattern])
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        except Exception as e:
            raise RuntimeError(
                f"grep (rg) failed for pattern={pattern!r} path={display_path!r}: {e}"
            ) from e
        lines: list[str] = []
        for line in (result.stdout or "").splitlines():
            rel_line = line
            prefix = str(root) + os.sep
            if line.startswith(prefix):
                rel_line = line[len(prefix) :]
            lines.append(truncate_line(rel_line))
            if len(lines) >= limit:
                lines.append(f"[Truncated: {limit} matches limit reached]")
                break
        body = "\n".join(lines) if lines else "No matches"
        is_error = result.returncode not in (0, 1)
        return self._wrap_output(body, exit_code=0 if not is_error else result.returncode, is_error=is_error)

    def _grep_file(self, pattern: str, path: str, root: os.PathLike, limit: int) -> ToolResult:
        results: list[str] = []
        try:
            text = open(root, encoding="utf-8", errors="replace").read()
        except OSError as e:
            raise OSError(f"grep read failed for path={path!r}: {e}") from e
        for i, line in enumerate(text.splitlines()):
            if re.search(pattern, line):
                results.append(f"{path}:{i + 1}: {truncate_line(line.strip())}")
                if len(results) >= limit:
                    break
        body = "\n".join(results) if results else "No matches"
        return self._wrap_output(body)

    def _grep_python(
        self, pattern: str, root: os.PathLike, glob_pattern: str, limit: int
    ) -> ToolResult:
        results: list[str] = []
        search_root = str(root)
        for match in glob_module.glob(os.path.join(search_root, glob_pattern), recursive=True):
            if not os.path.isfile(match):
                continue
            if any(s in match for s in SKIP_DIRS):
                continue
            try:
                with open(match, encoding="utf-8", errors="replace") as f:
                    for i, line in enumerate(f):
                        if re.search(pattern, line):
                            rel = os.path.relpath(match, search_root)
                            results.append(f"{rel}:{i + 1}: {truncate_line(line.strip())}")
                            if len(results) >= limit:
                                break
            except OSError:
                continue
            if len(results) >= limit:
                results.append(f"[Truncated: {limit} matches limit reached]")
                break
        body = "\n".join(results) if results else "No matches"
        return self._wrap_output(body)
