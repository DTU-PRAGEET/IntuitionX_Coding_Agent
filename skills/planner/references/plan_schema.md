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

## Triad Validation Checklist

Before emitting, confirm for each modified file:
- [ ] Every hypothesis rank has a corresponding step block
- [ ] READ step immediately before EDIT on same `file`
- [ ] VERIFY step immediately after EDIT on same `file`
- [ ] EDIT has `instruction` with inline acceptance checklist and `intent_marker`
- [ ] EDIT has `target_type` and `target_name`
- [ ] EDIT has `caution_flag` when target symbol appears multiple times
- [ ] All `file` paths ∈ `files_to_change` (unless `create_files=true`)
