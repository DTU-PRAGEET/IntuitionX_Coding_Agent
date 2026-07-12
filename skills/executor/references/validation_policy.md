<!-- CHANGELOG (2026-07-11): Relocated from executor.md — progressive context, Non-Errors, Out-of-scope detail. -->

---
# validation_policy.md
# Load when: executor semantic validation policy detail needed
---

## Validation Context Policy

The validator must judge the **completed edit**, not a small text match around a declaration line.

Context selection priority:

1. **Prefer git-diff changed regions** expanded to the nearest semantic parent
   (method / class / function / component method / config block).
2. **`target_type` / `target_name` are hints only** — they identify the likely vulnerable semantic
   region and guide expansion. They must **never** restrict the validation window to a declaration
   header ± a few lines.
3. If validation fails and missing requirements may lie outside the current snippet,
   **expand context and re-validate** before triggering Cursor repair.
4. **Whole-file validation** is allowed as fallback when:
   - semantic scope cannot be determined from the diff
   - the edit touches multiple locations
   - a source-to-sink security flow spans the file

**Never trigger Cursor repair until validation has seen enough context.**

### Progressive validation flow

```
Cursor edit (batch pass #1)
        |
        v
Build validation context: diff hunk -> semantic parent scope
        |
        v
LLM validator --PASS--> done
        |
       FAIL
        |
        v
Expand to full file -> validate again
        |
       FAIL
        |
        v
Cursor repair (batch pass #2) — only then
```

## Non-Errors (must NOT cause FAIL or be reported as an error)

The following are NOT failures — do not fail the step or list as `missing_requirements`:

- Whitespace, indentation-style, or line-ending differences that don't change behavior
- Cosmetic reordering of imports, parameters, or object keys
- A structural checker false-positive caused by template literals, JSX/TSX syntax,
  multi-line string interpolation, or decorators — if the LLM reads the modified
  snippet and the logic is coherent, do not fail on brace-counting alone
- Comments, TODOs, or logging added by Cursor alongside the requested change
- A different but equivalent implementation of the requested security fix
- Implementation shape differences (API choice, helper methods, variable names, call-chain
  style) when the security property is preserved
- Any issue that exists OUTSIDE the instruction's named scope (log as `observations` only)
- **`caution_flag` ambiguity** — target symbol appears multiple times; disambiguate using
  instruction and security context; do not fail because other occurrences remain unchanged

When uncertain whether something is a real failure or a non-error, prefer PASS
and log the concern in `observations`. Only FAIL when the specific requested
change is verifiably missing, reverted, or still vulnerable in the edited region.

## Out of scope (must NOT cause FAIL)

- Pre-existing secrets or vulnerabilities elsewhere in the file
- Unrelated properties added/changed by Cursor outside the instruction (note in `observations` only)
- Other ticket findings the planner did not assign to this EDIT step
- Overall file security posture
- General duplicate logic elsewhere in the file when instruction did not name a specific utility

## Observations and confidence

- Unrelated security issues may be listed in `observations` for logging.
- **Observations must not affect the `pass` boolean.**
- Set high confidence (≥ 0.9) only when the requested edit clearly satisfies the instruction
- Set low confidence (< 0.5) when the in-scope edit is ambiguous or partial
- Python trusts the `"pass"` boolean; confidence is logged for observability

## Retry guidance (after validation FAIL)

When Cursor is invoked again, address every item in `missing_requirements` from the prior validation.
Apply the planner `instruction` for **this step only**. Do not attempt to fix out-of-scope issues.

## Ambiguity handling (`caution_flag`)

When an EDIT step includes `caution_flag` from the planner:
- The target symbol (`target_type`/`target_name`) appears multiple times in the file
- The file path is correct — ambiguity is expected, not an error
- Use the `instruction` and security context to fix the correct occurrence
- Do not fail validation because duplicate occurrences elsewhere in the file remain unchanged
- Security issues may exist in one occurrence only; others may be tests, mocks, or unrelated reads
