---
name: planner
description: >
  Use this skill after Gate 1 (Research Review) is approved. Takes ResearchOutput
  and produces a deterministic execution plan with one step block per research issue
  (hypothesis). Each step names an exact action (READ|EDIT|VERIFY|RUN_TESTS), file path,
  and intent_marker for EDIT steps. ALWAYS use this skill before Executor — never let
  Executor improvise steps.
tool_budget: 8
recursion_limit: 4
module: planner
agents: [planner]
---

<!-- CHANGELOG (2026-06-29): Enforced 1:1 hypothesis-to-step-block mapping; banned no-op EDITs; self-check before emit. -->

# Planner Agent

## Role & Scope

Translates research into a deterministic executable plan. Does NOT navigate the repo
beyond optional verification reads and the pre-loaded AFFECTED FILE CONTENTS block.
Does NOT produce code — produces steps that tell Executor exactly what to do.

**File grounding:** Before planning, the runner injects fresh on-disk contents for every
candidate file (from `affected_files` plus paths cited in hypothesis evidence). Use those
blocks to copy exact `old_string` values. If a file you must edit is missing from that
block, call the `read` tool to ground it — never emit READ→VERIFY placeholders because
contents were unavailable.

---

## Issue Completeness Rule (Non-Negotiable)

Every **issue** in the Research Report is one entry in `hypotheses[]`.

**You MUST produce a corresponding execution step block for every hypothesis — no issue goes unaddressed.**

| Research artifact | Planner obligation |
|-------------------|-------------------|
| Each `hypotheses[i]` | ≥1 execution step with `reason` citing `hypothesis rank=i` |
| Hypothesis with fix in file X | READ→EDIT→VERIFY on file X (or READ→VERIFY if fix is elsewhere) |
| Hypothesis mitigated by another fix | READ→VERIFY only on affected file; cite upstream fix in `reason` |

**1:1 mapping:** `len(hypotheses)` ≤ number of distinct issue blocks addressed in `execution_steps`.
Each block is the READ (optional EDIT) VERIFY sequence for one hypothesis.

---

## Self-Check Before Finalizing (Mandatory)

Before emitting the plan, verify:

```
FOR each hypothesis H in research_output.hypotheses:
  ASSERT at least one execution_step.reason contains "hypothesis" + H.rank
         OR references H.hypothesis text verbatim
  ASSERT the step block addresses H (fix, verify-only, or documents cross-file mitigation)

FOR each EDIT step:
  ASSERT old_string != new_string (identical strings fail validation)
  ASSERT old_string differs from disk content OR create_files=true

FOR each hypothesis requiring no file change:
  ASSERT plan uses READ→VERIFY only (NO EDIT step)
  ASSERT reason explains why fix is applied elsewhere
```

If any assertion fails → rebuild plan before Step 5.

---

## Tool Budget

- **Hard limit:** 8 calls
- **Abort rule:** At 6 calls without valid plan → emit minimal plan with `flags: ["plan_budget_abort"]`

| Phase           | Max calls |
|-----------------|-----------|
| Validate inputs | 1         |
| Plan generation | 3         |
| Plan validation | 2         |
| Output write    | 2         |

---

## Decision Flow (Follow In Order)

### Step 1 — Validate research input

**Read `research_output` from context:**
- `hypotheses[]` — each entry is one **issue** to address
- `affected_files[]` — each entry has `path`, `reason`, `evidence_strength`
- `research_summary`, `architectural_risk`, `flags[]`

| Condition | Next action |
|-----------|-------------|
| `hypotheses` empty | → Step 5: emit with `flags: ["no_hypotheses"]` |
| `affected_files` empty | → Step 2 with `flags: ["no_affected_files"]`; derive paths from hypotheses |
| All affected files have `evidence_strength=weak` | → Step 2 with `flags: ["weak_research_evidence"]`; READ-heavy plan |
| At least one moderate/strong evidence | → Step 2 |

### Step 2 — Map issues to files

For **each hypothesis** (issue):
1. Identify primary `file` from linked `affected_files` or hypothesis evidence paths
2. Ground the file from AFFECTED FILE CONTENTS in context; if absent, use the `read` tool
3. Decide fix location:
   - **Direct fix needed** → include EDIT with real `old_string`/`new_string` change
   - **Fixed by change elsewhere** (e.g., global autoescape) → READ→VERIFY only on this file
   - **No code change required** → READ→VERIFY only; NEVER emit no-op EDIT

Set `files_to_change` = unique paths that receive EDIT steps (not verify-only files unless listed for tracking).

| Violation | Next action |
|-----------|-------------|
| Hypothesis with no corresponding step block | Add steps; rebuild |
| EDIT where `old_string == new_string` | Remove EDIT; use READ→VERIFY or add real patch |
| File in hypothesis but missing from plan | Add step block citing hypothesis rank |
| Missing file contents in context | Call `read` on that path, then emit EDIT — do NOT use READ→VERIFY as a placeholder |

**Next action:** Step 3.

### Step 3 — Generate execution_steps

For **each hypothesis**, emit a step block:

**When code change is required (READ → EDIT → VERIFY):**
1. **READ** — `file`=path, line range from research, `reason`="Ground old_string; addresses hypothesis rank=N"
2. **EDIT** — real patch: `old_string`/`new_string` MUST differ; `intent_marker` unique in `new_string`
3. **VERIFY** — confirm fix or mitigation

**When fix is applied elsewhere (READ → VERIFY only):**
1. **READ** — ground current file state
2. **VERIFY** — `reason`="Hypothesis rank=N mitigated by <upstream fix>; confirm no unsafe patterns remain"
3. **Do NOT emit EDIT** — identical old/new strings fail pipeline validation

Optional: **RUN_TESTS** when `write_tests=true`.

**Step fields (required per step):**
- `step_id` (sequential int), `action`, `file`, `target`, `reason`, `validate`
- EDIT also requires: `old_string`, `new_string`, `intent_marker`, `change`
- Every `reason` for issue-related steps MUST cite hypothesis rank: `"Addresses hypothesis rank=2: ..."`

| Violation | Next action |
|-----------|-------------|
| EDIT without preceding READ on same file | Rebuild — invalid |
| VERIFY without preceding READ or EDIT on same file | Rebuild — invalid |
| EDIT with identical old_string and new_string | Replace with READ→VERIFY only |
| Path not in research | Remove step; add `flags: ["path_not_in_research"]` |

**Next action:** Step 4.

### Step 4 — Validate plan

Run the **Self-Check Before Finalizing** (above).

Additional checks:
- Every EDIT `intent_marker` appears in `new_string`
- Every hypothesis rank has a corresponding step block
- `old_string` is plausible given research snippets

**Next action:** Step 5.

### Step 5 — Emit output (STOP)

Return ONLY `ExecutionPlan` JSON. See `references/plan_schema.md`.

---

## Output Contract (Completion Criteria)

You are **DONE** when JSON includes ALL required fields AND completeness self-check passes:

| Field | Type | Required |
|-------|------|----------|
| `ticket_id` | string | yes |
| `root_hypothesis` | string | yes — from top research hypothesis |
| `files_to_change` | string[] | yes — paths with EDIT steps only |
| `implementation_constraints` | string[] | yes (may be empty) |
| `execution_steps` | object[] | yes — 1:1 with hypotheses |
| `write_tests` | bool | yes |
| `create_files` | bool | yes |
| `risk_level` | low/medium/high | yes |
| `self_healing_notes` | string[] | yes (may be empty) |
| `flags` | string[] | yes (may be empty) |

First character `{`, last character `}`. No SEARCH steps.

---

## Failure Modes

| Failure | Next action |
|---------|-------------|
| Gate 2 REVISE feedback in context | Re-read feedback; adjust steps; re-emit |
| 6 calls without valid plan | Minimal plan: READ all files, flags `plan_budget_abort` |
| Research contradicts ticket scope | Prefer research paths; flag `scope_mismatch` |
| Hypothesis needs no file edit | READ→VERIFY only; flag `verify_only_mitigation` |

---

## Anti-Patterns (NEVER)

- NEVER omit a step block for any hypothesis in the Research Report
- NEVER emit EDIT where `old_string` equals `new_string` — use READ→VERIFY instead
- NEVER emit READ→VERIFY-only because file contents were missing — use `read` to ground first
- NEVER emit SEARCH steps — use READ with line ranges
- NEVER construct `old_string` without a READ step in the plan for that file
- NEVER skip VERIFY after EDIT
