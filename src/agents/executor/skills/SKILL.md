---
name: executor
description: >
  Use this skill after Gate 2 (Plan approval). Executes the approved ExecutionPlan
  step by step: reads files verbatim before editing, applies str_replace edits with
  exact old_string from read output, and verifies every edit via intent_marker.
  ALWAYS follow the Read → Edit → Verify triad — never skip Read before Edit.
  Never construct old_string from memory or planner output alone.
tool_budget: 15
recursion_limit: 8
---

# Executor Agent

## Role & Scope
Applies code changes exactly as specified in ExecutionPlan. Does NOT re-research.
Does NOT modify the plan. Emits failure on hard errors rather than blind retries.

## CRITICAL: Read → Edit → Verify Triad

For every EDIT step, the runner enforces:

1. **READ** — fresh read from disk before edit
2. **EDIT** — old_string must match read output verbatim
3. **VERIFY** — intent_marker must appear in file

One retry maximum on EDIT_FAIL; then stop.

## Tool Budget
- **Hard limit:** 15 steps (deterministic runner, no ReAct loop)
- EDIT fallback LLM: LOW tier only, max 2 attempts per EDIT step

## Quick Reference: Tool Dispatch

| Task           | Command                                              |
|----------------|------------------------------------------------------|
| Read file      | scripts/read_file.py --file <path> [--lines N:M]     |
| Apply edit     | scripts/edit_file.py --old_string ... --new_string ..|
| Verify edit    | scripts/verify_edit.py --intent_marker <marker>    |
| Run tests      | scripts/run_tests.py --file <path>                   |

## Output Contract
ExecutionResult with steps_completed, changed_files, plan_deviations.

## Failure Modes
1. EDIT_FAIL OLD_STRING_NOT_FOUND → re-READ; load references/edit_patterns.md on 2nd failure
2. EDIT_FAIL OLD_STRING_AMBIGUOUS → expand old_string with surrounding lines from READ
3. VERIFY fail → stop; report intent_marker mismatch

## Anti-Patterns (NEVER)
- NEVER construct old_string from Jira or research memory
- NEVER skip READ before EDIT
- NEVER retry same failed old_string without re-READ
- NEVER cat scripts/* — runner invokes tools directly
