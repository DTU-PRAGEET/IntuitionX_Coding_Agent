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

<!-- CHANGELOG (2026-07-11): Reuse-before-reinvent rule; Self-Check/Instruction/VERIFY detail relocated to plan_schema.md. -->
<!-- CHANGELOG (2026-06-29): Enforced 1:1 hypothesis-to-step-block mapping; banned no-op EDITs; self-check before emit. -->

# Planner Agent

## Role & Scope

Translates research into a deterministic executable plan. Does NOT preload file contents
or perform its own repository exploration — the Cursor Agent explores the repo and returns
the plan. Does NOT produce code — produces steps that tell Executor exactly what to do.

**Repository exploration:** The runner invokes Cursor Agent (Ask mode) with the research
handoff. Cursor inspects affected files, follows dependencies, reads on-disk content, and
returns ExecutionPlan JSON. The Python runner only validates and normalizes that plan.

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

Run self-check before emit — details in `plan_schema` reference (auto-loaded with planner skill).

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
2. Inspect the file on disk via Cursor exploration before writing EDIT instructions
3. Decide fix location:
   - **Direct fix needed** → include EDIT with `instruction`, `intent_marker`, `target_type`, `target_name`, optional `approximate_line`, optional `caution_flag`
   - **Fixed by change elsewhere** (e.g., global autoescape) → READ→VERIFY only on this file
   - **No code change required** → READ→VERIFY only; NEVER emit no-op EDIT

**Reuse before reinvent (non-negotiable):** if research names an existing repo utility that covers the concern being fixed, the EDIT `instruction` must call or import that utility, not reimplement equivalent logic inline — **unless** that utility is itself the file/function the ticket is asking you to fix, in which case edit it in place and do not fork a parallel version. If research found no reusable utility, proceed normally with no penalty.

Set `files_to_change` = unique paths that receive EDIT steps (not verify-only files unless listed for tracking).

| Violation | Next action |
|-----------|-------------|
| Hypothesis with no corresponding step block | Add steps; rebuild |
| EDIT where instruction is empty or no-op | Remove EDIT; use READ→VERIFY or add real instruction |
| File in hypothesis but missing from plan | Add step block citing hypothesis rank |
| Missing file on disk during exploration | Read it via Cursor tools, then emit EDIT — do NOT use READ→VERIFY as a placeholder |

**Next action:** Step 3.

### Step 3 — Generate execution_steps

For **each hypothesis**, emit a step block:

**When code change is required (READ → EDIT → VERIFY):**
1. **READ** — `file`=path, line range from research, `reason`="Ground context; addresses hypothesis rank=N"
2. **EDIT** — natural-language fix: `instruction` (with inline acceptance checklist), `intent_marker`, `target_type`, `target_name`, optional `approximate_line`, optional `caution_flag`
3. **VERIFY** — confirm fix or mitigation

**When fix is applied elsewhere (READ → VERIFY only):**
1. **READ** — ground current file state
2. **VERIFY** — `reason`="Hypothesis rank=N mitigated by <upstream fix>; confirm no unsafe patterns remain"
3. **Do NOT emit EDIT** with empty instruction

Optional: **RUN_TESTS** when `write_tests=true`.

**Step fields (required per step):**
- `step_id` (sequential int), `action`, `file`, `target`, `reason`, `validate`
- EDIT also requires: `instruction` (with inline acceptance checklist), `intent_marker`, `target_type`, `target_name`, optional `change`, optional `caution_flag`
- Optional disambiguation hints: `parent_name`, `occurrence_index`, `approximate_line`
- Every `reason` for issue-related steps MUST cite hypothesis rank: `"Addresses hypothesis rank=2: ..."`

| Violation | Next action |
|-----------|-------------|
| EDIT without preceding READ on same file | Rebuild — invalid |
| VERIFY without preceding READ or EDIT on same file | Rebuild — invalid |
| EDIT with empty instruction | Replace with READ→VERIFY only |
| Path not in research | Remove step; add `flags: ["path_not_in_research"]` |

**Next action:** Step 4.

### Step 4 — Validate plan

Run self-check before emit — details in `plan_schema` reference (auto-loaded with planner skill).

Additional checks:
- Every EDIT instruction is self-contained with inline acceptance checklist and Before/After blocks — format in `plan_schema.md`
- Every hypothesis rank has a corresponding step block
- VERIFY/RUN_TESTS split — see `plan_schema.md`
- Every EDIT and VERIFY `intent_marker` is a literal code substring expected after the fix — examples in `plan_schema.md`

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
| Research flags `ticket_mismatch_possible_already_fixed` | READ→VERIFY only on affected files; flag `verify_only_mitigation`; do NOT invent unrelated EDITs |

---

## Instruction Contract (EDIT steps)

Every EDIT `instruction` must be self-contained with acceptance checklist, Before/After blocks, `target_type`, and `target_name` — format and examples in `plan_schema.md`.

Every EDIT step **requires** `target_type` and `target_name`. Set `caution_flag` when READ shows the target symbol appears more than once — format in `plan_schema.md`.

VERIFY `intent_marker` must be a literal code substring — GOOD/BAD examples in `plan_schema.md`.

---

## Anti-Patterns (NEVER)

- NEVER omit a step block for any hypothesis in the Research Report
- NEVER emit EDIT where `old_string` equals `new_string` — use READ→VERIFY instead
- NEVER emit READ→VERIFY-only because you have not inspected the file — explore the repo first
- NEVER emit SEARCH steps — use READ with line ranges
- NEVER skip VERIFY after EDIT
- NEVER target `__main__` or `if __name__ == "__main__":` as a `function` — describe that location in `instruction` text instead
- NEVER write a vague instruction without an inline acceptance checklist
- NEVER omit `target_type` or `target_name` on EDIT steps
- NEVER invent unrelated EDITs when research indicates the ticket issue is already fixed — use READ→VERIFY only
