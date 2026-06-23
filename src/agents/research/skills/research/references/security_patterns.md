---
# security_patterns.md
# Sections: [Jira Enrichment ~15 lines] [Investigation Order ~20 lines] [SQLi/Auth ~15 lines]
# Total: ~50 lines
# Load when: ticket type is Security or label contains sqli/injection/cve
---

## Table of Contents
1. Jira Security Enrichment (line 10)
2. Investigation Order (line 25)
3. SQL Injection / Auth Focus (line 45)

---

## 1. Jira Security Enrichment

Before any tool call, read `security_enrichment` in context JSON:
- `cve_ids` — search for these strings and related patch references
- `severity` — informs architectural_risk when auth/security modules involved
- `vulnerability_type` — grep for matching patterns (SQLi, XSS, etc.)
- `affected_component` — start find/grep from this class or module name

## 2. Investigation Order (search-first)

1. Use file_index paths only — grep for vulnerability patterns
2. read — bounded line ranges on top 3–5 candidate files
3. bash — only for rg fallback; never modify files

Rank candidates by hit count from symbol search.

## 3. SQL Injection / Auth Focus

- SQL injection: grep for string concatenation in queries, raw JDBC, unparameterized statements
- Auth modules: trace entry points (controllers, filters) before data layer
- If auth/security modules affected → set `architectural_risk` to `high`

### Evidence rules
- Cite only file paths confirmed by find/grep/read
- If evidence insufficient: emit valid ResearchReport with limitations in `flags`
- Do not fabricate CVE fixes or file paths
