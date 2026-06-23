---
# plan_schema.md
# Example ExecutionPlan with READ|EDIT|VERIFY|RUN_TESTS
---

## Example Plan

```json
{
  "ticket_id": "PLAT-1234",
  "root_hypothesis": "...",
  "files_to_change": ["path/to/file.java"],
  "implementation_constraints": ["Do not modify public API"],
  "execution_steps": [
    {
      "step_id": 1,
      "action": "READ",
      "where": "path/to/file.java",
      "what": "Read target section before edit",
      "why": "Ground old_string from disk",
      "validate": "File read succeeds",
      "read_start_line": 40,
      "read_end_line": 80
    },
    {
      "step_id": 2,
      "action": "EDIT",
      "where": "path/to/file.java",
      "what": "Apply parameterized query fix",
      "why": "Fix SQL injection root cause",
      "validate": "grep shows PreparedStatement",
      "old_string": "exact text from READ",
      "new_string": "replacement text",
      "intent_marker": "PreparedStatement",
      "change": "human-readable fallback for executor LLM"
    },
    {
      "step_id": 3,
      "action": "VERIFY",
      "where": "path/to/file.java",
      "what": "Confirm fix applied",
      "why": "Ensure parameterized query present",
      "validate": "grep for PreparedStatement; grep for !concat",
      "intent_marker": "PreparedStatement"
    }
  ],
  "write_tests": false,
  "create_files": false,
  "risk_level": "low",
  "self_healing_notes": [],
  "flags": []
}
```

Triad rule: READ(step N) → EDIT(step N+1) → VERIFY(step N+2) for each modified file.
