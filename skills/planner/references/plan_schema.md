<!-- CHANGELOG (2026-06-26): Expanded to full ExecutionPlan field contract, RUN_TESTS example, flags/self_healing_notes, and triad validation checklist. -->

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
| `old_string` | — | yes* | — | — |
| `new_string` | — | yes | — | — |
| `intent_marker` | — | yes | recommended | — |
| `change` | — | recommended | — | — |
| `read_start_line` | optional | — | — | — |
| `read_end_line` | optional | — | — | — |

\* `old_string` may be empty when `create_files=true` (new file).

## Example Plan

```json
{
  "ticket_id": "PLAT-1234",
  "root_hypothesis": "SQL injection via string concatenation in UserDAO",
  "files_to_change": ["src/dao/UserDAO.java"],
  "implementation_constraints": ["Do not modify public API signatures"],
  "execution_steps": [
    {
      "step_id": 1,
      "action": "READ",
      "file": "src/dao/UserDAO.java",
      "target": "Read query method before edit",
      "reason": "Ground old_string from disk",
      "validate": "File read succeeds",
      "read_start_line": 40,
      "read_end_line": 80
    },
    {
      "step_id": 2,
      "action": "EDIT",
      "file": "src/dao/UserDAO.java",
      "target": "Parameterize SQL query",
      "reason": "Fix SQL injection root cause",
      "validate": "grep shows PreparedStatement",
      "old_string": "exact text from READ",
      "new_string": "replacement with PreparedStatement",
      "intent_marker": "PreparedStatement",
      "change": "Replace string-concat SQL with parameterized query"
    },
    {
      "step_id": 3,
      "action": "VERIFY",
      "file": "src/dao/UserDAO.java",
      "target": "Confirm fix applied",
      "reason": "Ensure parameterized query present",
      "validate": "grep for PreparedStatement; !string concatenation in SQL",
      "intent_marker": "PreparedStatement"
    },
    {
      "step_id": 4,
      "action": "RUN_TESTS",
      "file": "src/test/dao/UserDAOTest.java",
      "target": "Run unit tests for DAO",
      "reason": "Regression check after SQL fix",
      "validate": "Tests pass or skip"
    }
  ],
  "write_tests": true,
  "create_files": false,
  "risk_level": "medium",
  "self_healing_notes": ["If old_string mismatch, executor uses Cursor fallback"],
  "flags": []
}
```

## Issue Completeness (1:1 Mapping)

Each entry in `research_output.hypotheses[]` is one **issue**. The plan MUST address every issue:

| Research field | Plan requirement |
|----------------|------------------|
| `hypotheses[i].rank` | ≥1 `execution_steps` entry with `reason` citing `hypothesis rank=i` |
| Issue requiring code change | READ → EDIT → VERIFY on fix file |
| Issue mitigated elsewhere | READ → VERIFY only (no EDIT) |

**Self-check before emit:** For each hypothesis rank, confirm a corresponding step block exists.
Never emit EDIT where `old_string == new_string`.

## Triad Validation Checklist

Before emitting, confirm for each modified file:
- [ ] Every hypothesis rank has a corresponding step block
- [ ] READ step immediately before EDIT on same `file`
- [ ] VERIFY step immediately after EDIT on same `file`
- [ ] EDIT `old_string` ≠ `new_string` (identical strings fail validation)
- [ ] EDIT `intent_marker` is substring of `new_string`
- [ ] All `file` paths ∈ `files_to_change` (unless `create_files=true`)
