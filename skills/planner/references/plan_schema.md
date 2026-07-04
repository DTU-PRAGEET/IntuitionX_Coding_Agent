<!-- CHANGELOG (2026-07-03): Semantic EDIT steps — target_type/instruction replace old_string/new_string patches. -->

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
| `target_type` | — | yes | — | — |
| `target_name` | — | yes* | — | — |
| `instruction` | — | yes | — | — |
| `approximate_line` | optional | optional | — | — |
| `end_line` | — | block only | — | — |
| `parent_name` | — | optional | — | — |
| `occurrence_index` | — | optional | — | — |
| `intent_marker` | — | yes | recommended | — |
| `change` | — | recommended | — | — |
| `read_start_line` | optional | — | — | — |
| `read_end_line` | optional | — | — | — |

\* `target_name` optional for `target_type=file` or `block`.

### target_type values

`function` | `method` | `class` | `variable` | `assignment` | `import` | `block` | `file`

## Example Plan (semantic EDIT)

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
      "target_type": "function",
      "target_name": "validate_user",
      "approximate_line": 120,
      "instruction": "Replace the hardcoded JWT secret with an environment variable.",
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

## Triad Validation Checklist

Before emitting, confirm for each modified file:
- [ ] Every hypothesis rank has a corresponding step block
- [ ] READ step immediately before EDIT on same `file`
- [ ] VERIFY step immediately after EDIT on same `file`
- [ ] EDIT has `instruction`, `target_type`, `intent_marker`
- [ ] Semantic target resolves uniquely on disk (use `parent_name` / `occurrence_index` if ambiguous)
- [ ] All `file` paths ∈ `files_to_change` (unless `create_files=true`)
