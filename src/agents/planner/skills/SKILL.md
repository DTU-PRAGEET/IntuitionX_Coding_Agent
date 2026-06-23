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
---

# Planner Agent

## Role & Scope
Translates research into a deterministic executable plan. Does NOT read repository
code itself (trusts research affected_files). Does NOT produce code — produces
steps that tell Executor exactly what to do.

## Tool Budget
- **Hard limit:** 8 calls
- **Abort rule:** At 6 calls without valid plan, emit minimal plan with flags

| Phase           | Max calls |
|-----------------|-----------|
| Validate inputs | 1         |
| Plan generation | 3         |
| Plan validation | 2         |
| Output write    | 2         |

## Workflow

Step 1: [VALIDATE] Paths must come from research_output.affected_files:
  `python scripts/validate_plan.py --research /tmp/research.json --repo <repo>`

Step 2: [PLAN] Generate execution_steps. Every step includes:
  - action: READ | EDIT | VERIFY | RUN_TESTS
  - where: exact path from research
  - what, why, validate
  - intent_marker: unique string in new code (required for EDIT)
  - old_string/new_string for EDIT (from research evidence when possible)

Step 3: [VALIDATE PLAN] For every EDIT step confirm:
  - READ step immediately before on same file
  - VERIFY step immediately after on same file
  - intent_marker appears in new_string

Step 4: [EMIT] Return ONLY ExecutionPlan JSON

## CRITICAL: Read → Edit → Verify Triad

Every file modification MUST follow:
1. READ — confirm file exists; capture line range from research
2. EDIT — old_string/new_string patches; include intent_marker
3. VERIFY — intent_marker + validate grep patterns

Optional final RUN_TESTS when write_tests=true.

## Output Contract
Return ONLY valid ExecutionPlan JSON (see references/plan_schema.md).

## Failure Modes
1. EDIT without preceding READ on same file → invalid plan, rebuild
2. Plan modifies file not in affected_files → remove step, add flag
3. >8 steps for LOW risk → simplify

## Anti-Patterns (NEVER)
- NEVER emit SEARCH steps — use READ with line ranges instead
- NEVER construct old_string without READ step in plan
- NEVER skip VERIFY after EDIT
