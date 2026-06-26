<!-- CHANGELOG (2026-06-26): Rewrote for cursor_search-only workflow; removed file_index/grep/read/bash instructions that contradict research.md. -->

---
# security_patterns.md
# Load when: ticket type is Security OR labels contain security/cve/sqli
---

## Table of Contents
1. Jira Security Enrichment
2. Investigation Order (cursor_search)
3. SQL Injection / Auth Focus
4. Edge Cases

---

## 1. Jira Security Enrichment

Before any search, read `security_enrichment` in context JSON:
- `cve_ids` — include in first `cursor_search` query
- `severity` — if auth/security modules → `architectural_risk=high`
- `vulnerability_type` — tailor query (SQLi, XSS, path traversal)
- `affected_component` — use as anchor in search query

**Next action:** Step 2.

## 2. Investigation Order (cursor_search only)

1. `cursor_search(query="<vulnerability_type> in <affected_component>")`
2. Follow up: `cursor_search(query="string concatenation SQL <class from hit>")` for SQLi
3. Follow up: `cursor_search(query="authentication filter <module>")` for auth issues
4. Use hit `snippets` as evidence — do NOT call read/grep/find on repo files

Rank candidates by hit `confidence` (high > medium > low).

| Result | Next action |
|--------|-------------|
| No hits after 3 targeted queries | Broaden to module name; flag `security_search_sparse` |
| Hits but no snippets | Narrow query to file path from hit; flag `thin_snippets` if still empty |

**Next action:** Step 3.

## 3. SQL Injection / Auth Focus

- **SQLi:** Search for string concatenation in queries, raw JDBC, f-strings in SQL
- **Auth:** Search controllers/filters before data layer; trace entry points
- Auth/security modules affected → `architectural_risk=high`

### Evidence rules
- Cite only paths from `cursor_search` hits
- Evidence array entries: `"<path>: <snippet excerpt>"`
- Insufficient evidence → valid ResearchReport with limitations in `flags`

**Next action:** Emit ResearchReport (Step 5 in main skill).

## 4. Edge Cases

| Case | Next action |
|------|-------------|
| CVE in ticket but no dependency file in hits | `web_search` for advisory; flag `sca_follow_up_needed` |
| Multiple vulnerability types in labels | Prioritize SQLi > XSS > path traversal for first search |
| False positive suspected | Emit hypothesis with `evidence_strength=weak`, flag `possible_false_positive` |
