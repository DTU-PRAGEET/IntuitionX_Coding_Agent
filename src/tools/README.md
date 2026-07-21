# `src/tools/` — L1 Primitives

## Purpose

This directory implements **low-level, sandboxed file and shell tools** used by agents. These are the "hands" of the pipeline — read files, grep, edit, run bash — with path sandboxing, output truncation, and audit logging.

Agents do not import these files directly in most cases. They use `ToolRegistry` which wraps them.

## Architecture

```
Agent (research/planner/executor)
    → ToolRegistry(cwd=repo_path)
        → read_tool / grep_tool / edit_tool / bash_tool / ...
            → ToolResult { output, exit_code, is_error }
```

Research uses LangChain `StructuredTool` wrappers built on top of the same primitives via [`src/agents/research/tools/builder.py`](../agents/research/tools/builder.py).

Executor uses `RestrictedToolRegistry` with an allowlist from [`src/core/tool_permissions.yaml`](../core/tool_permissions.yaml).

## File layout

| File | Tool | Used by |
|------|------|---------|
| `registry.py` | `ToolRegistry` — registers all L1 tools for a workspace | Research, Planner, Executor |
| `base.py` | `ToolDefinition` ABC, audit wrapper, event emitter | All tools |
| `read_tool.py` | Read file with line range + truncation | Research, Planner, Executor |
| `write_tool.py` | Write file (create/overwrite) | Executor |
| `edit_tool.py` | Search-and-replace edit | Executor |
| `edit_args.py` | Normalizes edit tool arguments | edit_tool |
| `edit_diff.py` | Core diff/replace logic | edit_tool |
| `bash_tool.py` | Sandboxed shell execution | Research, Executor |
| `grep_tool.py` | Ripgrep-style content search | Research, Planner |
| `find_tool.py` | File discovery by name/pattern | Research, Planner |
| `ls_tool.py` | Directory listing | Research |
| `path_utils.py` | Path sandboxing — resolves paths relative to `cwd`, blocks escapes | All tools |
| `truncate.py` | Output truncation for read/grep (token budget) | read, grep |
| `__init__.py` | Package marker |

## ToolResult — how to read outputs

Every tool returns a `ToolResult` (defined in [`src/core/schemas.py`](../core/schemas.py)):

```python
ToolResult(
    output="...",      # stdout / file content / error message
    exit_code=0,       # 0 = success, non-zero = failure
    is_error=False,    # True if tool failed
    tool_name="read",
)
```

**Interpreting results:**
- `is_error=True` → agent should retry or report failure; check `output` for reason
- Truncated reads include a `... [truncated]` marker — agent may need a narrower line range
- Path errors mean the file is outside the sandboxed `cwd` or does not exist

## Per-agent tool access

Controlled by `tool_permissions.yaml`:

| Agent | Typical tools |
|-------|---------------|
| Research | read, ls, bash, grep, find, cursor_search, load_skill, web_search |
| Planner | read, grep, find (verification only) |
| Executor | read, bash (VERIFY/RUN_TESTS); edits go through Cursor, not local edit_tool |

## Path sandboxing

`path_utils.py` ensures all file operations stay within the repo checkout:
- Resolves relative paths against `ToolRegistry.cwd`
- Blocks `../` escapes outside the workspace
- Normalizes Cursor temp-clone paths back to repo-relative paths (via [`src/core/file_paths.py`](../core/file_paths.py))

## Related documentation

| Doc | Content |
|-----|---------|
| [`src/core/tool_permissions.yaml`](../core/tool_permissions.yaml) | Per-agent allowlists |
| [`docs/CODEBASE_MAP.md`](../../docs/CODEBASE_MAP.md) | L1 tools classification |
| [`docs/project_structure.md`](../../docs/project_structure.md) | Tool call trees in research/executor |
| [`AGENTS.md`](../../AGENTS.md) | Tool usage standards |
