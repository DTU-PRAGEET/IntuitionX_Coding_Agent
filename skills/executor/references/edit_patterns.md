<!-- CHANGELOG (2026-06-26): Updated for CursorGateway.edit() recovery path; added explicit next-action table per failure mode. -->

---
# edit_patterns.md
# Load when: 2nd+ EDIT_FAIL on same file (executor LLM fallback exhausted)
---

## Table of Contents
1. OLD_STRING_NOT_FOUND
2. OLD_STRING_AMBIGUOUS
3. Whitespace Traps
4. CursorGateway Failures

---

## 1. OLD_STRING_NOT_FOUND

**Symptom:** Cursor edit returns failure or post-read shows old_string absent.

| Step | Next action |
|------|-------------|
| 1 | Re-READ file from disk — never modify old_string from memory |
| 2 | Copy old_string verbatim from read output including blank lines |
| 3 | Strip line-number prefixes from read output before passing to Cursor metadata |
| 4 | Retry `CursorGateway.edit()` once with corrected old_string |
| 5 | If still failing → STOP; report in `plan_deviations` |

## 2. OLD_STRING_AMBIGUOUS

**Symptom:** old_string matches multiple locations.

| Step | Next action |
|------|-------------|
| 1 | Expand old_string with 2–3 surrounding lines from READ output |
| 2 | Ensure uniqueness before retry (one retry only) |
| 3 | Pass expanded strings in Cursor edit metadata |

## 3. Whitespace Traps

- Match indentation exactly (tabs vs spaces)
- Include trailing whitespace if present in file
- XML/HTML entities must match file content literally

**Next action:** Re-READ → rebuild old_string → single Cursor retry.

## 4. CursorGateway Failures

| Symptom | Next action |
|---------|-------------|
| `dry_run` result | Expected in dry-run mode; no file mutation |
| `exit_code != 0` | Check stderr; LLM fallback once; then STOP |
| No changed_files after edit | Re-READ; validate intent; accept if intent satisfied |
| Rate limit / SDK error | STOP; flag `cursor_edit_failed` in execution output |
