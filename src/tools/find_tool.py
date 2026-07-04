"""Find tool — fd/rg fallback with result limit."""

from __future__ import annotations

import glob as glob_module
import json
import os
import shutil
import subprocess

from src.core.schemas import FindInput, ToolResult
from src.tools.base import ToolDefinition, audit_tool

DEFAULT_LIMIT = 1000
SKIP_DIRS = (".git", "node_modules", "__pycache__", ".gradle", "/build/")


class FindTool(ToolDefinition):
    """Find files matching glob pattern. Scoped to workspace cwd."""

    name = "find"
    input_schema = FindInput

    @audit_tool
    def execute(self, pattern: str, path: str = ".", limit: int = DEFAULT_LIMIT) -> ToolResult:
        """Execute for FindTool."""
        try:
            root = self.resolve_path(path)
        except ValueError as e:
            return self._wrap_output(str(e), 1, True)
        if not root.is_dir():
            return self._wrap_output(f"Error: not a directory: {path}", 1, True)

        files: list[str] = []
        truncated = False
        warning: str | None = None

        if shutil.which("fd"):
            try:
                result = subprocess.run(
                    ["fd", "--glob", pattern, str(root)],
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                for line in result.stdout.splitlines():
                    rel = os.path.relpath(line.strip(), str(root))
                    if any(s in rel for s in SKIP_DIRS):
                        continue
                    files.append(rel)
                    if len(files) >= limit:
                        truncated = True
                        warning = f"[Truncated: {limit} results limit reached]"
                        break
            except Exception as e:
                raise RuntimeError(
                    f"find (fd) failed for pattern={pattern!r} path={path!r}: {e}"
                ) from e

        if not files:
            search_pattern = str(root / pattern) if not pattern.startswith("**/") else str(root / pattern)
            try:
                for match in glob_module.glob(search_pattern, recursive=True):
                    if not os.path.isfile(match):
                        continue
                    if any(s in match for s in SKIP_DIRS):
                        continue
                    files.append(os.path.relpath(match, str(root)))
                    if len(files) >= limit:
                        truncated = True
                        warning = f"[Truncated: {limit} results limit reached]"
                        break
            except Exception as e:
                raise RuntimeError(
                    f"find (glob) failed for pattern={pattern!r} path={path!r}: {e}"
                ) from e

        payload = {"files": files, "truncated": truncated, "warning": warning}
        return self._wrap_output(json.dumps(payload, indent=2))
