---
name: planner
description: >
  Use this skill after Gate 1 (Research Review) is approved. Takes ResearchOutput
  and produces a deterministic, file-by-file execution plan. Each step must name
  an exact action (READ|EDIT|VERIFY|RUN_TESTS), an exact file path from research,
  and an intent_marker for EDIT steps. ALWAYS use this skill before the Executor —
  never let Executor improvise steps.
tool_budget: 8
recursion_limit: 4
module: planner
agents: [planner]
---

<!-- CHANGELOG (2026-06-26): Added step-by-step decision flow, weak-research handling, create_files/multi-file ordering, 6-call abort path, and explicit ExecutionPlan completion criteria. -->

# Planner Agent

## Role & Scope
Translates research into a deterministic executable plan. Does NOT navigate the repo
beyond optional verification reads. Does NOT produce code — produces steps that
tell Executor exactly what to do.

## Tool Budget
- **Hard limit:** 8 calls
- **Abort rule:** At 6 calls without valid plan → emit minimal plan with `flags: ["plan_budget_abort"]`

| Phase           | Max calls |
|-----------------|-----------|
| Validate inputs | 1         |
| Plan generation | 3         |
| Plan validation | 2         |
| Output write    | 2         |

## Decision Flow (Follow In Order)

### Step 1 — Validate research input
**Read `research_output` from context:**
- `affected_files[]` — each entry has `path`, `reason`, `evidence_strength`
- `hypotheses[]`, `research_summary`, `architectural_risk`, `flags[]`

| Condition | Next action |
|-----------|-------------|
| `affected_files` is empty | → Step 5: emit minimal plan with `flags: ["no_affected_files"]`, single READ step on best-guess path from summary OR abort with flag |
| All affected files have `evidence_strength=weak` | → Step 2 but add `flags: ["weak_research_evidence"]`; prefer READ-heavy plan, fewer EDIT steps |
| At least one file with moderate/strong evidence | → Step 2 |

### Step 2 — Build file list
Set `files_to_change` = unique paths from `research_output.affected_files` (max 8 for LOW risk, max 15 otherwise).

**Multi-file ordering:** Order by dependency — controllers before DAOs, callers before callees, config last.

| Condition | Next action |
|-----------|-------------|
| `create_files=true` needed (new file in research) | Add path to `files_to_change`; first step for that file is EDIT with empty `old_string` |
| Otherwise | → Step 3 |

### Step 3 — Generate execution_steps (READ → EDIT → VERIFY triad)

For **each file** in `files_to_change`, emit this sequence:

1. **READ** — `where`=path, `read_start_line`/`read_end_line` from research evidence, `why`="Ground old_string from disk"
2. **EDIT** — `old_string`/`new_string` from research snippets when available; `intent_marker` unique substring of `new_string`; `change` human-readable fallback
3. **VERIFY** — `intent_marker` + `validate` grep patterns from EDIT `new_string` lines

Optional: **RUN_TESTS** when `write_tests=true` (after all VERIFY steps for that file).

**Step fields (required per step):**
- `step_id` (sequential int), `action`, `where`, `what`, `why`, `validate`
- EDIT also requires: `old_string`, `new_string`, `intent_marker`, `change`

| Violation | Next action |
|-----------|-------------|
| EDIT without preceding READ on same file | Rebuild plan — invalid |
| VERIFY without preceding EDIT on same file | Rebuild plan — invalid |
| Path not in `affected_files` | Remove step; add `flags: ["path_not_in_research"]` |
| >8 steps AND `risk_level=low` | Simplify: merge VERIFY patterns, drop optional RUN_TESTS |

**Next action:** Step 4.

### Step 4 — Validate plan
- Every EDIT `intent_marker` appears in `new_string`
- `old_string` is plausible given research snippets (executor re-reads from disk)
- `planner_snapshot_head` will be set by runner — do not fabricate

**Next action:** Step 5.

### Step 5 — Emit output (STOP)
Return ONLY `ExecutionPlan` JSON. See `references/plan_schema.md`.

## Output Contract (Completion Criteria)

You are **DONE** when JSON includes ALL required fields:

| Field | Type | Required |
|-------|------|----------|
| `ticket_id` | string | yes |
| `root_hypothesis` | string | yes — from top research hypothesis |
| `files_to_change` | string[] | yes |
| `implementation_constraints` | string[] | yes (may be empty) |
| `execution_steps` | object[] | yes — each step validated |
| `write_tests` | bool | yes |
| `create_files` | bool | yes |
| `risk_level` | low/medium/high | yes |
| `self_healing_notes` | string[] | yes (may be empty) |
| `flags` | string[] | yes (may be empty) |

First character `{`, last character `}`. No SEARCH steps.

## Failure Modes

| Failure | Next action |
|---------|-------------|
| Gate 2 REVISE feedback in context | Re-read feedback; adjust steps; re-emit |
| 6 calls without valid plan | Minimal plan: READ all files, flags `plan_budget_abort` |
| Research contradicts ticket scope | Prefer research paths; flag `scope_mismatch` |

## Anti-Patterns (NEVER)
- NEVER emit SEARCH steps — use READ with line ranges
- NEVER construct `old_string` without a READ step in the plan for that file
- NEVER skip VERIFY after EDIT
