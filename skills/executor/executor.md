---
name: executor
description: >
  Use this skill after Gate 2 (Plan approval). Executes the approved ExecutionPlan
  step by step: reads files from disk, applies edits ONLY via CursorGateway.edit(),
  and verifies every edit via intent_marker. ALWAYS follow Read → Cursor Edit → Verify.
  Never use write/edit tools directly — all file mutations go through CursorAgent.
tool_budget: 15
recursion_limit: 8
module: executor
agents: [implement, executor]
---

<!-- CHANGELOG (2026-06-26): Rewrote for CursorGateway.edit() as sole edit path; removed write/edit tool references; added decision flow, edge cases, ExecutionResult completion criteria. -->

# Executor Agent

## Role & Scope
Applies code changes exactly as specified in `ExecutionPlan`. Does NOT re-research.
Does NOT modify the plan. All **file mutations** go through `CursorGateway.edit()`.
Reads and verification use local `read` tool / disk reads only.

## CRITICAL: Read → Cursor Edit → Verify Triad

For every EDIT step, the runner enforces:

1. **READ** — fresh read from disk before edit (mandatory)
2. **EDIT** — `CursorGateway.edit(instruction, files=[path], metadata={old_string, new_string, ...})`
3. **VERIFY** — `intent_marker` and validate patterns must appear in file

One LLM fallback maximum per EDIT step (LOW tier); then stop.

## Tool Budget
- **Hard limit:** 15 steps (deterministic runner, no ReAct loop)
- **Allowed tools:** `read`, `bash` (reads/verify only); `write`/`edit` are **forbidden**
- EDIT fallback LLM: LOW tier, max 2 attempts per EDIT step

## Decision Flow (Follow In Order)

The runner processes `execution_steps` sequentially. For each step:

### READ step
1. Execute `read` tool with `where`, `read_start_line`, `read_end_line`
2. | Failure | Next action |
   |---------|-------------|
   | File not found | → STOP; return failure in ExecutionResult |
   | Success | → next step |

### EDIT step
1. **Pre-read** — `_read_file_snippet` from disk (even if READ step preceded)
2. Log pre-edit hash and `old_string` presence
3. | Condition | Next action |
   |-----------|-------------|
   | `create_files=true` and no `old_string` | → `CursorGateway.edit()` with create instruction |
   | `old_string == new_string` | → Check intent already satisfied; accept or fail |
   | Normal patch | → `CursorGateway.edit()` with plan old/new strings |
4. Post-edit: validate completeness and intent
5. | Primary edit failed/incomplete | Next action |
   |-------------------------------|-------------|
   | Fix intent already satisfied | Accept with `plan_deviations` note |
   | Otherwise | → LLM fallback → `CursorGateway.edit()` with fallback old/new (max 2 tries) |
6. | Still failing after fallback | → STOP pipeline; record error |

### VERIFY step
1. Check `intent_marker` via `verify_intent_marker`
2. Resolve required/forbidden patterns from prior EDIT steps + `validate` field
3. Read file content; grep-style substring checks
4. | Failure | → STOP |
   | Success | → next step |

### RUN_TESTS step
1. Invoke `run_tests_for_file` for `where` path
2. Exit code 2 = skipped (OK); 0 = pass; else fail

## Quick Reference: Dispatch

| Task | Backend |
|------|---------|
| Read file | `read` tool / `Path.read_text` |
| Apply edit | **`CursorGateway.edit()`** — NOT local edit/write tools |
| Verify edit | `verify_intent_marker` + pattern checks |
| Run tests | `run_tests_for_file` script |
| Git / PR | `create_worktree`, `show_diff`, `stage_commit_push`, `create_github_pr` |

## Output Contract (Completion Criteria)

Runner returns **`ExecutionResult`** when ALL steps succeed:

```json
{
  "ticket_id": "...",
  "changed_files": ["path/from/EDIT/steps"],
  "steps_completed": <int matching execution_steps length>,
  "step_results": [{"step_id", "action", "file", "ok", "output", "deviations"}],
  "plan_deviations": ["human-readable notes when fallback or intent-shortcut used"]
}
```

**Record `plan_deviations` when:**
- Plan `old_string` not in fresh read but edit succeeded
- LLM fallback used
- Fix intent satisfied without exact patch match
- Primary edit no-op but intent validated

**You are DONE** when all steps complete OR first failure raises (no silent skip).

## Failure Modes

| Failure | Next action |
|---------|-------------|
| `OLD_STRING_NOT_FOUND` after Cursor edit | Re-READ; LLM fallback once; load `references/edit_patterns.md` on 2nd failure |
| Cursor gateway dry_run | No file mutation; validation may pass with `dry_run` flag |
| Snapshot mismatch (`planner_snapshot_head` ≠ executor HEAD) | STOP before any EDIT |
| VERIFY intent_marker missing | STOP; report mismatch |
| Repository snapshot drift | STOP with RuntimeError |

## Anti-Patterns (NEVER)
- NEVER call `write` or `edit` tools — use `CursorGateway.edit()` only
- NEVER construct `old_string` from Jira or research memory without fresh read
- NEVER skip READ before EDIT
- NEVER retry same failed patch without re-READ
