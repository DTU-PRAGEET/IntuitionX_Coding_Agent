"""Hardened bash tool — timeout, process group, truncation."""

from __future__ import annotations

import os
import signal
import subprocess

from src.core.schemas import BashInput, ToolResult
from src.tools.base import ToolDefinition, audit_tool


class BashTool(ToolDefinition):
    """Run a shell command safely within workspace cwd."""

    name = "bash"
    input_schema = BashInput

    @audit_tool
    def execute(self, command: str, timeout: int = 30) -> ToolResult:
        """Execute for BashTool."""
        if not os.path.exists(self.cwd):
            return self._wrap_output(f"Error: cwd does not exist: {self.cwd}", 1, True)
        try:
            proc = subprocess.Popen(
                command,
                shell=True,
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                preexec_fn=os.setsid,
            )
            try:
                stdout, stderr = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                proc.wait()
                return self._wrap_output(
                    f"Error: command timed out after {timeout}s: {command!r}", 124, True
                )
            parts = []
            if stdout:
                parts.append(stdout)
            if stderr:
                parts.append(stderr)
            output = "".join(parts)
            if proc.returncode != 0 and not output:
                output = f"Command failed with exit code {proc.returncode}: {command!r}"
            is_error = proc.returncode != 0
            return self._wrap_output(output, exit_code=proc.returncode, is_error=is_error)
        except Exception as e:
            raise RuntimeError(f"bash failed for command={command!r} in cwd={self.cwd!r}: {e}") from e
