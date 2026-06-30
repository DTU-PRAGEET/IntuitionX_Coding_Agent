---
name: executor
description: >
  Use this skill after Gate 2 (Plan approval). Executes the approved ExecutionPlan by batching all file mutations into a single CursorGateway.edit() call. Reads and verification use local read/bash tools only. Cursor is called once and only once per pipeline run.
tool_budget: 15
recursion_limit: 8
module: executor
agents: [implement, executor]
---

<!-- CHANGELOG (2026-06-29): Enforced single consolidated Cursor call; batched payload structure. -->

# Executor Agent

## Role & Scope

Applies code changes exactly as specified in `ExecutionPlan`. Does NOT re-research.
Does NOT modify the plan. All **file mutations** go through **one** `CursorGateway.edit()` call.

---

## Cursor Call Rule (Non-Negotiable)

**Cursor is called once and only once. All edits are batched into a single call.**

- Collect every EDIT step from `execution_steps` before invoking Cursor
- Pass the full plan to Cursor in one consolidated payload
- Do NOT call Cursor per file, per fix, or per EDIT step
- Do NOT make incremental or iterative Cursor calls after the first

Local tools (`read`, `bash`) are used for READ steps, pre-edit grounding, and VERIFY steps.

---

## Single-Call Workflow

### Phase 1 — Pre-flight (local tools only)

1. Confirm `planner_snapshot_head` matches current repo HEAD — STOP on mismatch
2. Execute all **READ** steps sequentially via `read` tool; cache file contents
3. Validate each EDIT step's `old_string` against cached reads
4. Skip EDIT steps where fix intent is already satisfied (record in `plan_deviations`)

### Phase 2 — Consolidated Cursor edit (exactly one call)

Build one instruction and invoke `CursorGateway.edit()` **once**:

```json
{
  "instruction": "<imperative summary of all changes across all files>",
  "files": ["path/to/file1.py", "path/to/file2.jinja2"],
  "metadata": {
    "ticket_id": "<from plan>",
    "edits": [
      {
        "step_id": 2,
        "file": "path/to/file1.py",
        "old_string": "<exact from plan>",
        "new_string": "<exact from plan>",
        "intent_marker": "<from plan>",
        "change": "<human-readable summary>"
      },
      {
        "step_id": 5,
        "file": "path/to/file2.jinja2",
        "old_string": "...",
        "new_string": "...",
        "intent_marker": "...",
        "change": "..."
      }
    ],
    "create_files": false,
    "risk_level": "high"
  }
}
```

**Instruction template:**

```
Apply ALL of the following edits atomically in one pass. Do not stop after the first file.

Ticket: {ticket_id}
Risk: {risk_level}

For each edit in metadata.edits[]:
  1. Open edit.file
  2. Replace edit.old_string with edit.new_string (exact match)
  3. Confirm edit.intent_marker appears in the result

Edits to apply:
- [step {step_id}] {file}: {change}
- ...

If an old_string is not found, report which step failed — do not partially apply remaining edits without confirmation.
```

| Condition | Next action |
|-----------|-------------|
| Cursor returns success | → Phase 3 |
| Cursor partial failure | ONE LLM fallback re-packaging all remaining edits → **still only 1 Cursor retry total** |
| Cursor fails entirely | STOP; return ExecutionResult with error |

### Phase 3 — Verify (local tools only)

1. Execute all **VERIFY** steps via `read` + substring checks
2. Run **RUN_TESTS** steps if present
3. Emit **ExecutionResult**

---

## Tool Budget

- **Hard limit:** 15 steps (deterministic runner)
- **Cursor calls:** **1** (max 1 retry on total failure — still counts as single batched attempt)
- **Allowed tools:** `read`, `bash` (reads/verify only); `write`/`edit` are **forbidden**

---

## Quick Reference: Dispatch

| Task | Backend |
|------|---------|
| Read file | `read` tool |
| Apply ALL edits | **One `CursorGateway.edit()`** with consolidated `metadata.edits[]` |
| Verify edit | `read` + `verify_intent_marker` + pattern checks |
| Run tests | `run_tests_for_file` script |
| Git / PR | `create_worktree`, `show_diff`, `stage_commit_push`, `create_github_pr` |

---

## Output Contract (Completion Criteria)

Runner returns **`ExecutionResult`** when ALL steps succeed:

```json
{
  "ticket_id": "...",
  "changed_files": ["paths from EDIT steps"],
  "steps_completed": "<int matching execution_steps length>",
  "step_results": [{"step_id", "action", "file", "ok", "output", "deviations"}],
  "plan_deviations": ["notes when fallback or intent-shortcut used"],
  "cursor_calls": 1
}
```

**Record `plan_deviations` when:**
- Plan `old_string` not in fresh read but batched edit succeeded
- LLM fallback used on consolidated payload
- Fix intent satisfied without exact patch match
- EDIT steps skipped because intent already validated

**You are DONE** when all steps complete OR first failure raises (no silent skip).

---

## Failure Modes

| Failure | Next action |
|---------|-------------|
| `OLD_STRING_NOT_FOUND` in batched edit | Re-READ all affected files; rebuild consolidated payload; one retry |
| Cursor gateway dry_run | No file mutation; validation may pass with `dry_run` flag |
| Snapshot mismatch | STOP before Cursor call |
| VERIFY intent_marker missing | STOP; report mismatch |
| Second Cursor call attempted | STOP — violates single-call rule |

---

## Anti-Patterns (NEVER)

- NEVER call Cursor more than once per pipeline run (one batched call + at most one retry)
- NEVER call `CursorGateway.edit()` per file or per EDIT step
- NEVER call `write` or `edit` tools — use consolidated `CursorGateway.edit()` only
- NEVER construct `old_string` from Jira or research memory without fresh read
- NEVER skip READ before building consolidated payload
- NEVER retry same failed patch without re-READ
