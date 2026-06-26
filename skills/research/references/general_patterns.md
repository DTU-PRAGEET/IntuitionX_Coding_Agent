<!-- CHANGELOG (2026-06-26): Aligned with cursor_search evidence format; wired as default reference for all research tickets. -->

---
# general_patterns.md
# Load when: all research tickets (baseline evidence rules)
---

## Table of Contents
1. Evidence Rules (cursor_search)
2. Hypothesis Management
3. Confidence Scale
4. Edge Cases

---

## 1. Evidence Rules (cursor_search)

Every conclusion must cite `cursor_search` hit data:
- `path` from hit
- `snippet` line(s) supporting the claim
- `confidence` from hit (high/medium/low)

Never claim evidence not observed in search results.

**Valid evidence examples:**
- `"src/dao/User.py: snippet shows f-string in SQL query"`
- `"hit summary mentions unvalidated request parameter"`

| Insufficient snippets | Next action |
|-----------------------|-------------|
| Snippet < 2 lines | Narrow search to that path |
| Still insufficient | `evidence_strength=weak`, flag `thin_snippets` |

## 2. Hypothesis Management

- Max 3 hypotheses
- Each: `rank`, `hypothesis`, `evidence_strength`, `evidence[]`
- Discard weaker hypotheses when stronger evidence found

**Next action:** Assign confidence (Section 3).

## 3. Confidence Scale

| Level | Definition | Next action |
|-------|------------|-------------|
| **strong** | Root cause visible in snippets | Emit ResearchReport |
| **moderate** | Strong inference from multiple hits | Emit ResearchReport |
| **weak** | Path known, cause uncertain | Emit with `flags` explaining gap; do NOT search indefinitely |

Negative finding: if root cause not found, still emit ResearchReport with `flags: ["root_cause_not_found"]` listing queries tried.

## 4. Edge Cases

| Case | Next action |
|------|-------------|
| Empty search results | Rephrase query once; then flag `empty_search_results` |
| Ambiguous ticket scope | Prefer narrower module-scoped query; flag `ambiguous_scope` |
| No anchors in ticket | Derive query from `problem_statement` only |
| Conflicting hit paths | Prefer high-confidence hit; note conflict in flags |
