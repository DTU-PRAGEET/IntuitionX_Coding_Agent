# Agent Standards — Sprinklr Jira Solver

You are an AI coding agent with a modular tool architecture. Follow these rules:

## Tool Usage
- Prefer **read**, **write**, and **edit** for file modifications.
- Use **edit** for specific changes to avoid full-file rewrites.
- Use **bash** only for shell tasks like ls, grep, or running tests.
- Use **find** before **read** — search-first discovery.

## Context Management
- Never read the entire codebase.
- Use find or grep first to identify relevant files, then read specific code blocks.
- Pass structured JSON between phases — never raw tool logs.

## Error Handling
- If a tool fails, report with `is_error: true` in ToolResult so the LLM can attempt a corrective path.
- Do not retry blindly — stop and escalate after one failure per step.

## Atomicity
- Keep tool executions singular and well-defined.
- Do not chain complex operations in one tool call unless a composite tool is explicitly defined.

## Orchestration
- Routing intelligence lives in `src/agents/*/skills/SKILL.md` — not in Python.
- Python runners are thin: load skills, assemble context, call LLM, validate output, write state.
