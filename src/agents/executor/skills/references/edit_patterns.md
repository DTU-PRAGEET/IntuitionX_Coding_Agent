---
# edit_patterns.md
# Load when: 2nd+ EDIT_FAIL on same file
---

## Table of Contents
1. OLD_STRING_NOT_FOUND
2. OLD_STRING_AMBIGUOUS
3. Whitespace Traps

---

## 1. OLD_STRING_NOT_FOUND

- Re-READ the file — never modify old_string from memory
- Copy old_string verbatim from read output including blank lines
- Do not include line number prefixes from read output in old_string

## 2. OLD_STRING_AMBIGUOUS

- Expand old_string with 2–3 surrounding lines from READ output
- Ensure uniqueness before retry (one retry only)

## 3. Whitespace Traps

- Match indentation exactly (tabs vs spaces)
- Watch for trailing whitespace on lines
- XML/HTML entities must match file content literally
