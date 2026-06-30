<!-- CHANGELOG (2026-06-29): Aligned with read/ls/bash-first workflow and 2-call Cursor cap from research.md. -->

---
# security_patterns.md
# Load when: ticket type is Security OR labels contain security/cve/sqli
---

## Table of Contents
1. Jira Security Enrichment
2. Investigation Order (read/ls/bash first)
3. Cursor Budget (max 2)
4. SQL Injection / Auth Focus
5. Edge Cases

---

## 1. Jira Security Enrichment

Before any search, read `security_enrichment` and `vulnerability_category` in context JSON:
- `vulnerability_category` — load matching skill from `skills/security/` (e.g. Injection → `injection.md`)
- `cve_ids` — include in first `cursor_search` query (if Cursor is used)
- `severity` — if auth/security modules → `architectural_risk=high`
- `vulnerability_type` — tailor grep/read patterns (SQLi, XSS, path traversal)
- `affected_component` — use as anchor for `read` or `bash grep`

**Next action:** Step 2.

## 2. Investigation Order (read/ls/bash first)

1. If ticket names a file or component path → `read(path)` directly
2. If path unknown → `ls(module_root)` then `bash("grep -rn '<pattern>' path")` using patterns from the loaded security skill
3. `read` every grep hit path — ground evidence from file content
4. Only if paths remain unknown after grep → `cursor_search` (counts toward 2-call cap)

Rank candidates by direct code evidence in read output.

| Result | Next action |
|--------|-------------|
| Pattern found in read output | Build hypothesis with `evidence_strength=strong` |
| Grep hits but read inconclusive | Narrow read range; flag `thin_evidence` |
| No hits after grep in module | One `cursor_search` if budget remains; flag `security_search_sparse` if still empty |

**Next action:** Step 3.

## 3. Cursor Budget (max 2)

- Track Cursor calls — never exceed 2 per research run
- Prefer `bash grep -r` over Cursor for known vulnerability patterns (SQL concat, `autoescape=False`, etc.)
- After Cursor hit → mandatory `read` on returned paths

**Next action:** Step 4.

## 4. SQL Injection / Auth Focus

- **SQLi:** `grep -rn` for string concatenation in queries, f-strings in SQL, `%` formatting in execute calls
- **XSS:** `grep -rn` for `autoescape=False`, `|safe`, `Markup(`, `innerHTML=`
- **Auth:** Read route handlers and middleware before data layer

### Evidence rules
- Cite only paths confirmed by `read` output
- Evidence array entries: `"<path>:L<line> <excerpt>"`
- Insufficient evidence → valid ResearchReport with limitations in `flags`

**Next action:** Emit ResearchReport (Step 5 in main skill).

## 5. Edge Cases

| Case | Next action |
|------|-------------|
| CVE in ticket but no dependency file in grep hits | `web_search` for advisory; flag `sca_follow_up_needed` |
| Multiple vulnerability types in labels | Classify each type using its security skill; one hypothesis per type |
| False positive suspected | Emit hypothesis with `evidence_strength=weak`, flag `possible_false_positive` |
| Cursor budget exhausted | Continue with grep/read only; flag `cursor_budget_exhausted` |
