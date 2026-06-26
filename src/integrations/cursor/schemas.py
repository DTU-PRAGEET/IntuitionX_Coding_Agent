"""Structured types for CursorGateway search and edit results."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from src.core.schemas import FixPlan


class CursorSearchHit(BaseModel):
    path: str
    confidence: Literal["high", "medium", "low"]
    summary: str
    snippets: list[str] = Field(default_factory=list)


class CursorSearchResponse(BaseModel):
    query: str
    hits: list[CursorSearchHit] = Field(default_factory=list)
    flags: list[str] = Field(default_factory=list)


class CursorEditRequest(BaseModel):
    """Typed edit input for CursorGateway.edit()."""

    instruction: str
    files: list[str] = Field(default_factory=list)
    fix_plan: FixPlan | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CursorEditResult(BaseModel):
    """Structured result from gateway.edit() — replaces raw CLI output."""

    status: str
    text: str
    dry_run: bool = False
    method: str = "edit"
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    git_diff_stat: str = ""
    git_diff_full: str = ""
    changed_files: list[str] = Field(default_factory=list)
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)

    def to_history_entry(self) -> dict[str, Any]:
        return {
            "method": self.method,
            "status": self.status,
            "dry_run": self.dry_run,
            "exit_code": self.exit_code,
            "text_len": len(self.text),
            "text_preview": self.text[:500] if self.text else "",
            "changed_files": self.changed_files,
            "git_diff_stat": self.git_diff_stat[:500] if self.git_diff_stat else "",
            "tool_calls": self.tool_calls,
        }
