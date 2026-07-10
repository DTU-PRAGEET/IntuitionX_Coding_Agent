---
name: executor
description: >
  Use this skill after Gate 2 (Plan approval). Executes the approved ExecutionPlan by batching ALL edit steps into at most two whole-plan CursorGateway.edit() calls. Post-edit validation uses LLM semantic validation driven by this policy document. VERIFY steps use local read/bash pattern checks.
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

**Always batch ALL EDIT steps** into batch pass #1 (single Cursor call), regardless of file independence or chained same-file edits.
If validation fails for any step, at most **one repair batch call** (pass #2) is permitted for the failed steps only.

---

## Cursor Call Budget

Configured via `CURSOR_MAX_EDIT_ATTEMPTS` (default **2** whole-plan batch passes).

| Pass | Action |
|------|--------|
| Pass 1 | Single `CursorGateway.edit()` with **ALL** EDIT steps |
| Pass 2 | Single repair `CursorGateway.edit()` with **only failed steps** + retry notes (only if pass 1 validation had failures) |

**Rules:**
- Never invoke Cursor more than `cursor_max_edit_attempts` batch passes for the entire plan (default 2)
- Pass 2 is permitted **only** when LLM semantic validation fails for one or more steps after pass 1
- Never invoke Cursor a third time — if steps still fail after pass 2, STOP and report all failures together
- When `cursor_max_edit_attempts=1`, pass 2 is skipped entirely

Local tools (`read`, `bash`) are used for READ steps, pre-edit snippet capture, and VERIFY steps.

---

## Single-Call Workflow

### Phase 1 — Pre-flight (local tools only)

1. Confirm `planner_snapshot_head` matches current repo HEAD — STOP on mismatch
2. Execute all **READ** steps sequentially via `read` tool
3. Capture pre-edit snippet from `approximate_line` or prior READ line range (no symbol resolution)
4. Skip EDIT steps where LLM validation confirms fix intent is already satisfied (record in `plan_deviations`)

### Phase 2 — Cursor edit (two batch passes max)

1. **Batch pass #1:** send ALL EDIT steps in one `CursorGateway.edit()` call
2. Validate every edited step with **progressive context** (noop + structural + LLM semantic):
   - Level 1: git-diff changed lines expanded to semantic parent (method/class/function)
   - Level 2: full-file fallback when Level 1 fails (before Cursor repair)
3. If all pass → continue to Phase 3
4. **Batch pass #2:** re-apply ONLY failed steps (single call, with retry notes from validation)
5. Re-validate only previously-failed steps (same progressive context policy)
6. If any still fail → STOP; report **all** remaining failures in one error

| Condition | Next action |
|-----------|-------------|
| Cursor returns success | → LLM semantic validation per step |
| Semantic validation passes for all steps | → Phase 3 |
| Semantic validation fails for some steps and pass 2 available | → batch pass #2 repair |
| Semantic validation fails after pass 2 | STOP; aggregate error listing every failing step |
| Cursor batch incomplete | Log deviation; still validate and attempt pass 2 if failures remain |

### Phase 3 — Verify (local tools only)

1. Execute all **VERIFY** steps via `read` + `verify_intent_marker` + pattern checks (unchanged)
2. Run **RUN_TESTS** steps if present
3. Emit **ExecutionResult**

---

## Structural Pre-Validation (deterministic, before LLM)

After Cursor returns, run structural checks before LLM semantic validation:

- **Python (`.py`):** `ast.parse()` — syntax errors auto-fail without LLM review
- **TS/JS/TSX/JSX and other brace languages:** run brace/paren balance check but treat mismatches as **warnings** passed into the LLM prompt — the semantic validator makes the final call using the actual modified snippet (template literals, JSX, and decorators can cause false positives)
- Structural warnings must NOT alone cause FAIL — only Python syntax errors are hard gates

---

## Cursor Instruction Template

Python passes the planner `instruction` verbatim (it already contains the acceptance checklist).
Executor adds only mechanical constraints:
- intent_marker must appear in file after edit
- modify only the named file

Executor does NOT synthesize a generic acceptance checklist.

On retry, fold prior failure into the instruction:
- noop_diff → suggest adding file snippet or approximate_line
- structural_fail → list structural errors
- validation_fail → list missing_requirements from LLM validator

---

## Semantic Validation Policy

After Cursor completes an edit (or reports no-op), the Executor runs **progressive LLM validation** per attempt.
This is NOT a separate agent and NOT a ReAct loop — structured reasoning with context expansion.

**Role: security-intent verifier, NOT whole-file security reviewer.**

The validator answers: **Does the modified code satisfy the requested security property?**
It does **not** check whether the implementation matches the planner's preferred shape.
It does **not** re-audit the entire file for unrelated vulnerabilities.

### Validation Context Policy

The validator must judge the **completed edit**, not a small text match around a declaration line.

Context selection priority:

1. **Prefer git-diff changed regions** expanded to the nearest semantic parent
   (method / class / function / component method / config block).
2. **`target_type` / `target_name` are hints only** — they identify the likely vulnerable semantic
   region and guide expansion. They must **never** restrict the validation window to a declaration
   header ± a few lines.
3. If validation fails and missing requirements may lie outside the current snippet,
   **expand context and re-validate** before triggering Cursor repair.
4. **Whole-file validation** is allowed as fallback when:
   - semantic scope cannot be determined from the diff
   - the edit touches multiple locations
   - a source-to-sink security flow spans the file

**Never trigger Cursor repair until validation has seen enough context.**

Flow:

```
Cursor edit (batch pass #1)
        |
        v
Build validation context: diff hunk -> semantic parent scope
        |
        v
LLM validator --PASS--> done
        |
       FAIL
        |
        v
Expand to full file -> validate again
        |
       FAIL
        |
        v
Cursor repair (batch pass #2) — only then
```

### Scope (strict)

- Evaluate **only** the planner `instruction` applied to the code region in the snippets below.
- Compare the **Original Code** and **Modified Code** snippets (diff-expanded semantic block, or full-file fallback).
- Do not fail because hardcoded secrets, weak patterns, or intentional insecure code exist **outside** this step's scope.
- Other profiles, properties, or sections not named in the instruction are out of scope.

### Acceptance criteria (PASS)

Pass when the requested **security property** is satisfied in the modified snippet:
- Helper function extraction
- Variable or parameter renaming
- Different API with same security guarantee (e.g. `${ENV:default}` vs `os.getenv`,
  `parseClaimsJws(...).getBody()` vs direct `Claims` cast, alternate JWT parser APIs)
- Equivalent refactoring, formatting, whitespace, comments
- Fix already present before edit (no-op is OK if instruction satisfied)

Judge the actual Modified Code shown — do not infer compile/type errors unless the snippet
clearly shows broken syntax.

### Rejection criteria (FAIL — in-scope only)

Fail only when the **requested security property** is not satisfied:
- Requested security change missing or partially implemented **in the edited target**
- The specific vulnerability named in the instruction still present in the edited target
- Edit inverted or weakened the specific security behavior named in the instruction

### Non-Errors (must NOT cause FAIL or be reported as an error)

The following are NOT failures, even if they look unusual — do not fail the step
or list these as `missing_requirements`:

- Whitespace, indentation-style, or line-ending differences that don't change behavior
- Cosmetic reordering of imports, parameters, or object keys
- A structural checker false-positive caused by template literals, JSX/TSX syntax,
  multi-line string interpolation, or decorators — if the LLM reads the modified
  snippet and the logic is coherent, do not fail on brace-counting alone
- Comments, TODOs, or logging added by Cursor alongside the requested change
- A different but equivalent implementation of the requested security fix (see Acceptance
  Criteria above)
- Implementation shape differences (API choice, helper methods, variable names, call-chain
  style) when the security property is preserved
- Any issue that exists OUTSIDE the instruction's named scope (log as `observations` only)
- **`caution_flag` ambiguity** — when the planner set `caution_flag`, the target symbol appears multiple times in the file. The file path is correct. Disambiguate using the instruction and security context. Do not fail because other occurrences of the same symbol remain unchanged (e.g. test mocks, unrelated reads). The security fix may apply to only one occurrence.

When uncertain whether something is a real failure or a non-error, prefer PASS
and log the concern in `observations`. Only FAIL when the specific requested
change is verifiably missing, reverted, or still vulnerable in the edited region.

### Ambiguity handling (`caution_flag`)

When an EDIT step includes `caution_flag` from the planner:
- The target symbol (`target_type`/`target_name`) appears multiple times in the file
- The file path is correct — ambiguity is expected, not an error
- Use the `instruction` and security context to fix the correct occurrence
- Do not fail validation because duplicate occurrences elsewhere in the file remain unchanged
- Security issues may exist in one occurrence only; others may be tests, mocks, or unrelated reads

---

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
- **Cursor calls:** up to `cursor_max_edit_attempts` whole-plan batch passes (default 2)
- **LLM calls:** progressive semantic validation per EDIT step per batch pass (diff-semantic, then full-file fallback)
- **Allowed tools:** `read`, `bash` (reads/verify only); `write`/`edit` are **forbidden**

---

## Quick Reference: Dispatch

| Task | Backend |
|------|---------|
| Read file | `read` tool |
| Apply edits | `CursorGateway.edit()` (always batched — pass 1 all steps, pass 2 failed only) |
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
- Batch pass 2 repair was required after pass 1 validation failures
- Fix intent satisfied on Cursor no-op (file already correct)

**You are DONE** when all steps complete OR first failure raises (no silent skip).

---

## Failure Modes

| Failure | Next action |
|---------|-------------|
| Semantic validation FAIL after max batch passes | STOP if repair batch was not run; otherwise soft-fail, record warnings, and continue to PR |
| Cursor gateway dry_run | No file mutation; validation may pass with `dry_run` flag |
| Snapshot mismatch | STOP before Cursor call |
| VERIFY intent_marker missing | STOP; report mismatch |

---

## Anti-Patterns (NEVER)

- NEVER invoke Cursor more than `cursor_max_edit_attempts` whole-plan batch passes
- NEVER retry Cursor when semantic validation passed
- NEVER use literal grep/substring matching as the primary EDIT pass/fail gate
- NEVER fail EDIT validation because of vulnerabilities outside the planner instruction scope
- NEVER treat the semantic validator as a whole-file security audit — that is VERIFY's job
- NEVER call `write` or `edit` tools — use `CursorGateway.edit()` only
- NEVER construct `old_string` from Jira or research memory without fresh read
- NEVER skip pre-edit snippet capture before batched Cursor call
