<!-- CHANGELOG (2026-07-11): Self-check ASSERT, Before/After format, verification taxonomy, VERIFY intent_marker examples relocated from planner.md. -->
<!-- CHANGELOG (2026-07-06): Required semantic targeting — target_type + target_name; caution_flag for ambiguity. -->

---
# plan_schema.md
# Level-3 reference — load on planner trigger
---

## Required ExecutionPlan Fields

| Field | Type | Required |
|-------|------|----------|
| `ticket_id` | string | yes |
| `root_hypothesis` | string | yes |
| `files_to_change` | string[] | yes |
| `implementation_constraints` | string[] | yes |
| `execution_steps` | ExecutionStep[] | yes |
| `write_tests` | bool | yes |
| `create_files` | bool | yes |
| `risk_level` | low \| medium \| high | yes |
| `self_healing_notes` | string[] | yes |
| `flags` | string[] | yes |

## Required ExecutionStep Fields

| Field | READ | EDIT | VERIFY | RUN_TESTS |
|-------|------|------|--------|-----------|
| `step_id` | yes | yes | yes | yes |
| `action` | yes | yes | yes | yes |
| `file` | yes | yes | yes | yes |
| `target` | yes | yes | yes | yes |
| `reason` | yes | yes | yes | yes |
| `validate` | yes | yes | yes | yes |
| `instruction` | — | yes | — | — |
| `intent_marker` | — | yes | recommended | — |
| `target_type` | — | **yes** | — | — |
| `target_name` | — | **yes** | — | — |
| `approximate_line` | optional | optional | — | — |
| `caution_flag` | — | optional* | — | — |
| `parent_name` | — | optional | — | — |
| `occurrence_index` | — | optional | — | — |
| `change` | — | recommended | — | — |
| `read_start_line` | optional | — | — | — |
| `read_end_line` | optional | — | — | — |

\* `caution_flag` required when READ shows the target symbol appears more than once in the file.

`target_type` values: `function`, `method`, `class`, `variable`, `assignment`, `import`, `block`, `file`.

## Example Plan (instruction-based EDIT with semantic targeting)

```json
{
  "ticket_id": "PLAT-1234",
  "root_hypothesis": "Hardcoded JWT secret in validate_user",
  "files_to_change": ["src/auth.py"],
  "implementation_constraints": ["Do not modify public API signatures"],
  "execution_steps": [
    {
      "step_id": 1,
      "action": "READ",
      "file": "src/auth.py",
      "target": "Read validate_user before edit",
      "reason": "Ground context for hypothesis rank=1",
      "validate": "File read succeeds"
    },
    {
      "step_id": 2,
      "action": "EDIT",
      "file": "src/auth.py",
      "target": "Parameterize JWT secret",
      "reason": "Fix hypothesis rank=1: committed secret",
      "validate": "grep for os.environ",
      "approximate_line": 120,
      "target_type": "function",
      "target_name": "validate_user",
      "instruction": "In validate_user, replace the hardcoded JWT secret with an environment variable. Do not change public API signatures. Acceptance: (1) os.environ or equivalent used, (2) no hardcoded secret string, (3) function still returns token.",
      "intent_marker": "os.environ",
      "change": "Use env var for JWT secret"
    },
    {
      "step_id": 3,
      "action": "VERIFY",
      "file": "src/auth.py",
      "target": "Confirm fix applied",
      "reason": "Ensure secret removed",
      "validate": "grep for os.environ",
      "intent_marker": "os.environ"
    }
  ],
  "write_tests": false,
  "create_files": false,
  "risk_level": "medium",
  "self_healing_notes": [],
  "flags": []
}
```

## Example Plan (duplicate symbol with caution_flag)

```json
{
  "step_id": 2,
  "action": "EDIT",
  "file": "frontend/src/app/search-result/search-result.component.ts",
  "target": "Fix searchValue sanitizer bypass",
  "reason": "Fix hypothesis rank=1: direct assignment bypasses sanitizer",
  "validate": "grep for bypassSecurityTrustHtml",
  "target_type": "variable",
  "target_name": "this.searchValue",
  "approximate_line": 42,
  "caution_flag": "CAUTION: 'this.searchValue' appears 4 times (lines 42, 67, 89, 112). The security issue is the direct assignment in ngOnInit (~line 42). Other occurrences may be test mocks — do not fail if unchanged.",
  "instruction": "In ngOnInit, stop assigning user input directly to this.searchValue; route through DomSanitizer. Do not modify test mocks. Acceptance: (1) sanitizer used, (2) no direct bypass, (3) component still renders.",
  "intent_marker": "bypassSecurityTrustHtml"
}
```

## Self-Check Before Finalizing (Mandatory)

Before emitting the plan, verify:

```
FOR each hypothesis H in research_output.hypotheses:
  ASSERT at least one execution_step.reason contains "hypothesis" + H.rank
         OR references H.hypothesis text verbatim
  ASSERT the step block addresses H (fix, verify-only, or documents cross-file mitigation)

FOR each EDIT step:
  ASSERT instruction describes a real security fix (not no-op)
  ASSERT instruction is specific enough for a human engineer to locate and apply the fix
  ASSERT instruction names what NOT to touch (e.g. "do not restructure surrounding braces/control flow")
  ASSERT instruction includes an inline acceptance checklist: "Acceptance: (1) ..., (2) ..., (3) ..."
  ASSERT every EDIT instruction includes Before/After blocks (see format below)
  ASSERT intent_marker is expected in file after Executor applies the fix
  ASSERT target_type and target_name identify the edit site (required on every EDIT)
  ASSERT caution_flag is set when READ shows the target symbol appears more than once
  ASSERT approximate_line is provided when helpful (optional, never required to be exact)

FOR each hypothesis requiring no file change:
  ASSERT plan uses READ→VERIFY only (NO EDIT step)
  ASSERT reason explains why fix is applied elsewhere
```

If any assertion fails → rebuild plan before emit.

## Triad Validation Checklist

Before emitting, confirm for each modified file:
- [ ] Every hypothesis rank has a corresponding step block
- [ ] READ step immediately before EDIT on same `file`
- [ ] VERIFY step immediately after EDIT on same `file`
- [ ] EDIT has `instruction` with inline acceptance checklist and `intent_marker`
- [ ] EDIT has `target_type` and `target_name`
- [ ] EDIT has `caution_flag` when target symbol appears multiple times
- [ ] All `file` paths ∈ `files_to_change` (unless `create_files=true`)

## Instruction Contract (EDIT steps)

Every EDIT `instruction` MUST be self-contained and include:
1. **What to change** — concrete, locatable in the file (name the function/block/line region in plain language)
2. **What NOT to touch** — e.g. "do not restructure surrounding braces/control flow"
3. **Acceptance checklist** — 3–5 numbered items Cursor self-verifies, inline in the instruction:
   "Acceptance: (1) ..., (2) ..., (3) ..."
4. **Before/After blocks** — see format below

The acceptance checklist lives IN the instruction field. Python and Executor do NOT add a parallel checklist.

## Before/After Format (EDIT instructions)

Every EDIT `instruction` must include Before/After blocks shaped like repo code — not pseudocode:

```
Before (current — from READ):
  [actual lines from file]

After (target — what executor should produce):
  [concrete code showing the fix, matching repo style]
```

## Semantic Targeting (EDIT steps)

Every EDIT step **requires** `target_type` and `target_name`:
- `target_type`: one of `function`, `method`, `class`, `variable`, `assignment`, `import`, `block`, `file`
- `target_name`: symbol or identifier (e.g. `execute`, `this.searchValue`, `validate_user`)
- For `target_type: file`, `target_name` may be the filename

Use `parent_name` and `occurrence_index` when the symbol is nested or repeated.

## caution_flag Format Example

When READ shows the target symbol appears **more than once**, set `caution_flag`:

```
CAUTION: 'this.searchValue' appears 4 times in search-result.component.ts (lines 42, 67, 89, 112). The security issue is the direct assignment bypassing sanitizer in ngOnInit (~line 42). Other occurrences may be test mocks or unrelated reads — do not fail if unchanged.
```

Do NOT emit `caution_flag` when the symbol is unique — use `approximate_line` instead.

## VERIFY intent_marker Contract

`intent_marker` for VERIFY steps MUST be a literal code token or exact substring expected verbatim after the fix.

**GOOD (literal, code-shaped):**
- `expect(component.searchValue).toBe`
- `os.environ`
- `bcrypt.checkpw`

**BAD (prose describing intent):**
- `searchValue is assigned directly`
- `the function now validates input properly`

If verification is behavioral rather than literal, route through EDIT semantic validation — do not invent a VERIFY prose marker.

Python does literal fact-checks on VERIFY; LLM does judgment on EDIT.

## Verification Taxonomy (VERIFY / RUN_TESTS)

| Step type | Automated (agent-runnable) | Manual (Gate 2 / human) |
|-----------|---------------------------|---------------------------|
| VERIFY | `intent_marker` literal match in file | Behavioral checks → document in `reason`, not VERIFY marker |
| RUN_TESTS | Named test command from repo conventions | — |
