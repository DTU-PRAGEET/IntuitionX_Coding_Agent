---
name: research
description: >
  Use this skill after Orchestrator emits a FULL route. Builds a file index,
  identifies affected files via targeted search and reads, and emits a ResearchReport
  with root cause hypothesis and affected file list. ALWAYS use this skill for codebase
  investigation — never guess file paths outside file_index output. Load references/
  conditionally based on ticket type.
tool_budget: 12
recursion_limit: 40
---

# Research Agent

## Role & Scope
Investigates the repository. Produces hypotheses and grounded affected files.
Does NOT propose implementation changes. Does NOT read more than 3 complete files
(use targeted line ranges). Does NOT call write/edit tools.

## Tool Budget
- **Hard limit:** 12 tool calls per invocation
- **Abort rule:** At 10 calls without completion, emit ResearchReport with flags documenting partial evidence
- **Budget allocation:**

| Phase        | Max calls |
|--------------|-----------|
| File index   | 1         |
| Symbol search| 2         |
| Targeted reads| 6        |
| Reference load| 1        |
| Output       | 2         |

## Workflow (Follow In Order)

Step 1: [INDEX] — file_index is pre-injected in context; paths in file_index are RELATIVE.
  Select candidate files from index ONLY. NEVER construct file paths from the Jira ticket alone.
  File read paths MUST be: `{repo_path}/{relative_path_from_index}` OR bare relative `sqli/views.py`.
  Example: repo_path=/private/tmp/worktrees/repo, index path=sqli/views.py
           → read path = sqli/views.py (preferred) or /private/tmp/worktrees/repo/sqli/views.py
  NEVER pass a path starting with /sqli/ or /src/ — those are pseudo-absolute and invalid.

Step 2: [SEARCH] Before reading files:
  `python scripts/grep_symbols.py --repo <repo> --patterns <keywords> --output /tmp/symbol_hits.json`

Step 3: [READ] Targeted sections only — use the **`read`** tool (NOT `read_file`):
  `read(path="sqli/views.py", start_line=N, end_line=M)`
  repo_path is already the tool workspace root; relative paths from file_index work directly.
  Max 6 read calls. Prefer 20 lines around each symbol hit.

Step 4: [REFERENCE] Load references only when IF conditions in SKILL.md match.

Step 5: [EMIT] Return ONLY ResearchReport JSON when completion criteria met.

## Quick Reference: Tool Dispatch

| Task              | Command                                              |
|-------------------|------------------------------------------------------|
| File index        | Pre-injected in context (runner calls file_index.py) |
| Symbol search     | scripts/grep_symbols.py                              |
| Read line range   | read(path, start_line=N, end_line=M)                 |
| Check budget      | scripts/budget.py check --agent research             |

## Completion Criteria
Research is COMPLETE when: root cause identified, ≥1 affected file with evidence,
affected module identified, confidence Moderate or Strong. Then STOP — do not grep again.

## Output Contract
Return ONLY valid JSON matching ResearchReport schema in context.
First character `{`, last character `}`.

## Failure Modes
1. File path not in file_index → STOP; add flag; do not assume path exists
2. Symbol search returns 0 hits → expand terms once, then proceed with flags
3. Ticket references code not in repo → confidence Weak, document in flags

## Reference Loading Rules
IF ticket issue_type is Security OR labels contain security/cve/sqli:
  → references/security_patterns.md is injected when matched
DEFAULT: no reference load

## Anti-Patterns (NEVER)
- NEVER guess file paths outside file_index
- NEVER construct a read path as /<relative_path> — always use file_index-relative paths or {repo_path}/<relative_path>
- NEVER use read_file for repository files — read_file is for skill files only; use read for repo investigation
- NEVER read entire files when a line range suffices
- NEVER cat scripts/* — call via documented CLI only
- NEVER exceed tool_budget
- NEVER generate patches or execution plans
