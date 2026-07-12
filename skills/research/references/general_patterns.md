<!-- CHANGELOG (2026-07-11): Tool selection tree relocated from research.md; hypothesis max aligned to 10. -->

---
# general_patterns.md
# Load when: all research tickets (baseline evidence rules)
---

## Table of Contents
1. Evidence Rules
2. Hypothesis Management
3. Confidence Scale
4. Edge Cases
5. Tool Selection Decision Tree

---

## 1. Evidence Rules

Every conclusion must cite evidence from `read` output or search hits:
- `path:line` or `path:start-end` from file read
- `snippet` line(s) supporting the claim

Never claim evidence not observed in read or search results.

**Valid evidence examples:**
- `"src/dao/User.py:42 — f-string in SQL query"`
- `"grep hit at api/views.py:89 shows unvalidated parameter"`

| Insufficient evidence | Next action |
|-----------------------|-------------|
| Snippet < 2 lines | Narrow read range to that path |
| Still insufficient | `evidence_strength=weak`, flag `thin_snippets` |

## 2. Hypothesis Management

- Max **10** hypotheses (min 1)
- Each: `rank`, `hypothesis`, `evidence_strength`, `evidence[]`
- Discard weaker hypotheses when stronger evidence found

**Next action:** Assign confidence (Section 3).

## 3. Confidence Scale

| Level | Definition | Next action |
|-------|------------|-------------|
| **strong** | Root cause visible in read output | Emit ResearchReport |
| **moderate** | Strong inference from multiple reads/hits | Emit ResearchReport |
| **weak** | Path known, cause uncertain | Emit with `flags` explaining gap; do NOT search indefinitely |

Negative finding: if root cause not found, still emit ResearchReport with `flags: ["root_cause_not_found"]` listing queries tried.

## 4. Edge Cases

| Case | Next action |
|------|-------------|
| Empty search results | Rephrase query once; then flag `empty_search_results` |
| Ambiguous ticket scope | Prefer narrower module-scoped query; flag `ambiguous_scope` |
| No anchors in ticket | Derive query from `problem_statement` only |
| Conflicting hit paths | Prefer high-confidence hit; note conflict in flags |

## 5. Tool Selection Decision Tree (Follow In Order)

```
START
  │
  ├─ repo_path missing? ──YES──► emit ResearchReport, flags: ["missing_repo_path"]
  │
  NO
  │
  ├─ anchors[] or ticket names exact file path(s)?
  │     YES ──► read(path) for each ──► synthesize (Step 4)
  │
  NO
  │
  ├─ FIRST MOVE: cursor_search(query, path=".")  [1 of 2]
  │     └─► read() the 4-5 paths it returns ──► gather evidence
  │
  ├─ location still unclear AND codebase large AND cursor_calls < 2?
  │     YES ──► cursor_search(narrowed query)  [2 of 2] ──► read hits
  │     NO  ──► bash(grep -rn 'pattern' path) / ls(path) ──► read matches
  │
  └─ synthesize (Step 4) and STOP
```

| Situation | Tool | Never use |
|-----------|------|-----------|
| Locate code when paths unknown (opening move) | `cursor_search(query, path=".")` (call 1 of 2) | read on a guessed path |
| Read a file cursor_search surfaced | `read(path, start_line, end_line)` | cursor_search |
| Read one already-known file (anchor) | `read(path, start_line, end_line)` | cursor_search |
| List directory | `ls(path)` | cursor_search |
| Search pattern in a located subtree | `bash("grep -rn 'pattern' path")` | cursor_search |
| Find files by name | `bash("find path -name '*.py'")` | cursor_search |
| Large repo, location still unclear | `cursor_search` (call 2 of 2) | a 3rd cursor_search |
| External CVE/advisory | `web_search` | cursor_search |
