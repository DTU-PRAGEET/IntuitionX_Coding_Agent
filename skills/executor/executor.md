---
name: executor
description: >
  Use this skill after Gate 2 (Plan approval). Executes the approved ExecutionPlan by batching independent file mutations into a single CursorGateway.edit() call when possible. Post-edit validation uses LLM semantic validation driven by this policy document. VERIFY steps use local read/bash pattern checks.
tool_budget: 15
recursion_limit: 8
module: executor
agents: [implement, executor]
---

<!-- CHANGELOG (2026-07-04): LLM semantic post-edit validation; configurable per-step Cursor retry budget. -->

# Executor Agent

## Role & Scope

Applies code changes exactly as specified in `ExecutionPlan`. Does NOT re-research.
Does NOT modify the plan. File mutations go through `CursorGateway.edit()`.

When edits are independent (one EDIT per file), batch them into a **single upfront Cursor call**.
When validation fails for a batched file, at most **one repair Cursor call** is permitted for that file.

---

## Cursor Call Budget

Configured via `CURSOR_MAX_EDIT_ATTEMPTS` (default **2** per EDIT step).

| Scenario | Cursor calls per EDIT step |
|----------|---------------------------|
| Normal (non-batched) | 1 on success; 2 only if semantic validation fails after attempt 1 |
| Batched (independent edits) | Batched call counts as attempt 1; 1 repair call if validation fails |
| Chained same-file edits | Sequential per-step path; each step gets its own budget |

**Rules:**
- Never invoke Cursor more than `cursor_max_edit_attempts` times for a single EDIT step
- Second Cursor call is permitted **only** when LLM semantic validation fails after the first
- Never invoke Cursor a third time for the same step

Local tools (`read`, `bash`) are used for READ steps, pre-edit snippet capture, and VERIFY steps.

---

## Single-Call Workflow

### Phase 1 — Pre-flight (local tools only)

1. Confirm `planner_snapshot_head` matches current repo HEAD — STOP on mismatch
2. Execute all **READ** steps sequentially via `read` tool
3. Resolve each EDIT step's semantic target on disk; capture **original snippet** before any Cursor call
4. Skip EDIT steps where LLM validation confirms fix intent is already satisfied (record in `plan_deviations`)

### Phase 2 — Cursor edit

**Independent edits:** one batched `CursorGateway.edit()` up front, then per-step LLM validation.

**Chained same-file edits:** one Cursor call per EDIT step (sequential).

| Condition | Next action |
|-----------|-------------|
| Cursor returns success | → LLM semantic validation |
| Semantic validation passes | → next step |
| Semantic validation fails and attempts remain | → one Cursor repair with retry note from validation |
| Semantic validation fails with no attempts left | STOP; return ExecutionResult with error |
| Cursor fails (non no-op) | STOP immediately |

### Phase 3 — Verify (local tools only)

1. Execute all **VERIFY** steps via `read` + `verify_intent_marker` + pattern checks (unchanged)
2. Run **RUN_TESTS** steps if present
3. Emit **ExecutionResult**

---

## Semantic Validation Policy

After Cursor completes an edit (or reports no-op), the Executor runs **one LLM validation call** per attempt.
This is NOT a separate agent and NOT a ReAct loop — a single structured reasoning step.

**Role: edit verifier, NOT whole-file security reviewer.**

The validator confirms the planner's requested change was correctly applied to the semantic target.
It does **not** re-audit the entire file for unrelated vulnerabilities.

### Scope (strict)

- Evaluate **only** the planner `instruction` applied to the resolved semantic target.
- Compare the **Original Code** and **Modified Code** snippets provided (edit scope + immediate context).
- Do not fail because hardcoded secrets, weak patterns, or intentional insecure code exist **outside** this step's scope.
- Other profiles, properties, or sections not named in the instruction are out of scope.

### Acceptance criteria (PASS)

Pass when the requested edit is correctly implemented in the modified snippet:
- Helper function extraction
- Variable or parameter renaming
- Different API with same behavior (e.g. `${ENV:default}` vs `os.getenv`)
- Equivalent refactoring, formatting, whitespace, comments
- Fix already present before edit (no-op is OK if instruction satisfied)

### Rejection criteria (FAIL — in-scope only)

Fail only when the **requested change** was not correctly implemented:
- Requested change missing or partially implemented **in the edited target**
- The specific property/behavior named in the instruction still has the vulnerability it asked to remove
- Edit inverted or weakened the specific fix named in the instruction

### Out of scope (must NOT cause FAIL)

- Pre-existing secrets or vulnerabilities elsewhere in the file
- Unrelated properties added/changed by Cursor outside the instruction (note in `observations` only)
- Other ticket findings the planner did not assign to this EDIT step
- Overall file security posture

### Observations

- Unrelated security issues may be listed in `observations` for logging.
- **Observations must not affect the `pass` boolean.**

### Confidence guidance

- Set high confidence (≥ 0.9) only when the requested edit clearly satisfies the instruction
- Set low confidence (< 0.5) when the in-scope edit is ambiguous or partial
- Python trusts the `"pass"` boolean; confidence is logged for observability

### Retry guidance (after validation FAIL)

When Cursor is invoked again, address every item in `missing_requirements` from the prior validation.
Apply the planner `instruction` for **this step only**. Do not attempt to fix out-of-scope issues.

---

## Tool Budget

- **Hard limit:** 15 steps (deterministic runner)
- **Cursor calls:** up to `cursor_max_edit_attempts` per EDIT step (default 2)
- **LLM calls:** one semantic validation per Cursor attempt per EDIT step
- **Allowed tools:** `read`, `bash` (reads/verify only); `write`/`edit` are **forbidden**

---

## Quick Reference: Dispatch

| Task | Backend |
|------|---------|
| Read file | `read` tool |
| Apply edits | `CursorGateway.edit()` (batched or per-step) |
| Validate EDIT | LLM semantic validation (`validation.py`) |
| Verify step | `read` + `verify_intent_marker` + pattern checks |
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
  "plan_deviations": ["notes when retry or intent-shortcut used"]
}
```

**Record `plan_deviations` when:**
- Batched edit required per-file repair after validation failure
- Semantic edit required one Cursor retry
- Fix intent satisfied on Cursor no-op (file already correct)

**You are DONE** when all steps complete OR first failure raises (no silent skip).

---

## Failure Modes

| Failure | Next action |
|---------|-------------|
| Semantic validation FAIL after max attempts | STOP; report `reason` and `missing_requirements` |
| Cursor gateway dry_run | No file mutation; validation may pass with `dry_run` flag |
| Snapshot mismatch | STOP before Cursor call |
| VERIFY intent_marker missing | STOP; report mismatch |
| Cursor non no-op failure | STOP immediately (no validation retry loop) |

---

## Anti-Patterns (NEVER)

- NEVER invoke Cursor more than `cursor_max_edit_attempts` times per EDIT step
- NEVER retry Cursor when semantic validation passed
- NEVER use literal grep/substring matching as the primary EDIT pass/fail gate
- NEVER fail EDIT validation because of vulnerabilities outside the planner instruction scope
- NEVER treat the semantic validator as a whole-file security audit — that is VERIFY's job
- NEVER call `write` or `edit` tools — use `CursorGateway.edit()` only
- NEVER construct `old_string` from Jira or research memory without fresh read
- NEVER skip pre-edit snippet capture before batched Cursor call
