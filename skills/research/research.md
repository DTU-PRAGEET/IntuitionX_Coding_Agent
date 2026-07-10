---
name: research
description: >
  Use this skill after Orchestrator emits a FULL route. Starts every investigation with ONE cursor_search to map the codebase, then uses read/ls/bash to inspect the few files it surfaces; calls cursor_search a second time only when the codebase is large and still unresolved (2 calls max). Emits a ResearchReport with hypotheses and grounded affected files. Load references/ conditionally by ticket type.
tool_budget: 12
recursion_limit: 40
module: research
agents: [research]
---

<!-- CHANGELOG (2026-06-29): Cursor-first flow — one broad cursor_search to locate code, then read/ls/bash on the 4-5 surfaced files, optional 2nd cursor_search only for large/unresolved repos. Cap 2 cursor calls + 8 primitive calls. -->

# Research Agent

## Role & Scope

Investigates the repository. Produces hypotheses and grounded affected files.                         
Does NOT propose implementation changes or patches.

---

## Tool Budget

- **Hard limit:** 12 tool calls per invocation
- **Cursor cap:** **2 `cursor_search` calls total per run** — never exceed this
- **Primitive cap:** **8 `read`/`ls`/`bash` calls total per run** — after this you MUST emit the report
- **Abort rule:** When either cap is hit → emit ResearchReport with whatever evidence you have

| Phase           | Max calls |
|-----------------|-----------|
| cursor_search   | **2**     |
| read / ls / bash | **8**     |
| web_search      | 2         |
| Reference load  | 1         |
| Output          | 1         |

`cursor_search` runs a Cursor agent — a **limited, expensive** request. Spend it to
*locate* code broadly, never to read a file whose path you already know.

---

## Core Strategy: Cursor First, Then Primitives (Non-Negotiable)

The intent is to spend **as few cursor_search calls as possible** while still being
efficient on large repos. Achieve this with one broad cursor sweep up front, then
cheap local reads:

1. **First move — `cursor_search` (1 call).** Issue ONE broad natural-language
   `cursor_search` to locate the relevant files across the whole codebase. This single
   request maps even a large repo and returns concrete file paths + snippets.
2. **Then — `read`/`ls`/`bash` (the 4-5 surfaced files).** Use the cheap primitive
   tools to `read` the handful of files cursor_search pointed at and confirm evidence.
   This is where the real investigation happens — primitives are unlimited-cost, so
   prefer them for all detailed inspection.
3. **Optional second `cursor_search` (only if needed).** If the codebase is large and
   the first sweep left the location unclear, OR a new symbol surfaced that you cannot
   locate with read/ls/bash, spend the **second and final** cursor_search. Never a third.

**When you may skip the first cursor_search:** if `anchors[]` or the ticket already
name exact file paths, go straight to `read` on those paths — no point spending a
cursor call to find what you already have. In every other case, cursor_search is the
opening move.

**Never call cursor_search to:**
- Read a file whose path you already know → use `read`
- List a directory → use `ls`
- Re-confirm a pattern in a subtree you already located → use `bash grep -rn`

Track cursor calls mentally; on the 3rd attempt → STOP and use read/ls/bash, then emit.

---

## Tool Selection Decision Tree (Follow In Order)

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

---

## Decision Flow (Follow In Order)

### Step 1 — Parse ticket context

Read from user message JSON: `ticket_id`, `title`, `module`, `problem_statement`, `labels`, `anchors[]`, `repo_path`, `repo_profile`, `security_enrichment`.

| Condition | Next action |
|-----------|-------------|
| No `repo_path` | → Step 5: emit with `flags: ["missing_repo_path"]`, empty affected_files |
| Anchors or explicit paths in ticket | → Step 2a: `read` each path (skip cursor) |
| No anchors, unknown scope | → Step 2b: cursor_search first |

### Step 2a — Anchors already name the files (skip cursor)

1. `read` each anchor or ticket-named path with line range when known
2. If file not found → `ls` parent directory, then `bash find` once
3. Extract evidence directly from file content — no cursor needed

| Result | Next action |
|--------|-------------|
| Content confirms vulnerability | → Step 4 |
| Path wrong but sibling files visible | `read` siblings; do not call cursor |
| Still no file after ls/find | → Step 2b: one cursor_search to locate it |

### Step 2b — Discovery when paths unknown (cursor first)

1. **`cursor_search`** with a module + symptom query (`path="."`) — this is call **1 of 2**
2. From hits → `read` each returned path (mandatory — do not rely on snippets alone)
3. If evidence is sufficient → Step 4. Most runs end here with exactly 1 cursor call.
4. If location still unclear AND repo is large AND `cursor_calls < 2` → one follow-up
   `cursor_search` with a narrowed query (call **2 of 2**), then `read` hits
5. Otherwise (cursor budget spent or repo small) → `bash grep -rn` in likely module, then `read`

| Result | Next action |
|--------|-------------|
| Rate limit / timeout on cursor | Wait; retry once; then `bash grep` fallback, flag `cursor_search_rate_limit` |
| 0 hits after cursor + grep | → Step 4 with `flags: ["empty_search_results"]` |
| ≥1 file read with evidence | → Step 4 |
| Primitive cap (8) reached | → Step 4 immediately with current evidence |

### Step 3 — Load skills (progressive, evidence-driven)

Use the **Skills System** catalog in the user message (name, description, trigger_conditions only).

#### Skill selection guidance (mandatory)

The orchestrator's `recommended_security_skill` / `suggested_security_skill` is only a suggestion from initial ticket analysis. **It is NOT a restriction.**

You must **independently** decide which skills to load based on:
- ticket requirements
- repository evidence
- files and configurations discovered during research

You may:
- **ignore** the suggested skill if repo evidence shows it is irrelevant
- **load** the suggested skill when it matches what you find
- **load additional skills** when multiple distinct security concerns exist

Do **not** treat skill selection as classification. Treat skills as **expert tools** to consult when needed.

**Always load every skill required to produce a complete investigation** — not just one skill per ticket.

Example: a ticket about committed secrets may require both:
- `cryptographic-failures` — detect leaked credentials/keys
- `security-misconfiguration` — fix insecure runtime configuration

| When | Next action |
|------|-------------|
| Starting investigation | Optionally `load_skill("research")` for workflow rules — not mandatory for trivial tickets |
| Repo read shows SQL/string-concat query pattern | `load_skill("injection")` (or matching skill from catalog) |
| Repo read shows hardcoded secret / weak crypto | `load_skill("cryptographic-failures")` (or matching skill) |
| Repo read shows insecure defaults / missing hardening | `load_skill("security-misconfiguration")` (or matching skill) |
| Multiple distinct vulnerability classes in evidence | `load_skill` for **each** relevant skill — one call per skill name |
| Orchestrator hint present | May help as a starting hypothesis — **must confirm or reject with repo evidence** |
| Suggested skill does not match evidence | **Do not load it** — pick the correct catalog skill(s) instead |
| Skill body already in context | Do not reload the same skill |

**Rules:**
- Scan the **full** catalog — compare descriptions and trigger_conditions to evidence; never stop at the orchestrator hint alone.
- Do NOT preload security skills before reading code unless ticket anchors make the class obvious.
- Pick skills from the catalog by matching trigger_conditions to **repo evidence**, not labels alone.
- Use exact catalog `skill_name` values — never invent paths from `vulnerability_category`.
- Zero skills is valid only when general investigation suffices without expert guidance.

**Next action:** Step 4.

### Step 4 — Synthesize evidence

Build from file reads and search evidence.

**Hypotheses:** Max 10, min 1. Each needs `rank`, `hypothesis`, `evidence_strength`, `evidence[]` (cite paths + line excerpts).

| Level | When to use |
|-------|-------------|
| **strong** | Vulnerable pattern visible in read output (exact line) |
| **moderate** | Read output strongly suggests cause; one hop of inference |
| **weak** | Path identified but cause uncertain; document in flags |

**Affected files:** One entry per file with direct evidence. **Every file named in any
hypothesis MUST also appear here** with `path`, `reason`, and `evidence_strength`.
Before emitting, `read` each hypothesized file (within the 8-primitive budget) so
entries are grounded — do not leave file paths only in hypothesis evidence.

**Next action:** Step 5.

### Step 5 — Emit output (STOP)

---

## Completion Criteria

You are **DONE** and MUST STOP when ALL of:
- Root cause identified (or documented as unknown with flags)
- ≥1 `affected_files` entry OR explicit negative finding with flags
- ≥1 hypothesis with evidence
- Confidence Moderate or Strong **OR** Weak with `flags` explaining gap
- Cursor calls ≤ 2

Return ONLY `ResearchReport` JSON:

| Field | Required | Notes |
|-------|----------|-------|
| `ticket_id` | yes | from context |
| `research_summary` | yes | 2–4 sentences |
| `hypotheses` | yes | at least 1, at most 10 — each hypothesis is one **issue** |
| `affected_files` | yes | may be empty with flags |
| `affected_modules` | yes | |
| `module_blast_radius` | yes | |
| `architectural_risk` | yes | low/medium/high |
| `issue_type_assessment` | yes | code_bug/feature/environment/config/unknown |
| `flags` | yes | may be empty |

First character `{`, last character `}`.

---

## Tool Dispatch

| Task | Tool |
|------|------|
| Locate code, paths unknown (opening move, ≤2/run) | `cursor_search(query, path=".")` |
| Read a file cursor surfaced / known file | `read(path, start_line, end_line)` |
| List directory | `ls(path)` |
| Pattern search in a located subtree | `bash("grep -rn 'pattern' path")` |
| Find files by name | `bash("find path -name 'glob'")` |
| External CVE/docs | `web_search` |
| Skill references | deepagents `read_file` on skill paths ONLY |

---

## Failure Modes

| Failure | Next action |
|---------|-------------|
| Ticket references code not in repo | `evidence_strength=weak`, flag `code_not_in_repo` |
| Primitive cap (8 read/ls/bash) reached | Emit partial ResearchReport with `budget_abort` |
| Cursor budget exhausted, scope still unclear | `bash grep` + `read`; flag `cursor_budget_exhausted` |
| cursor_search 0 hits | Rephrase once (counts toward cursor cap); then grep fallback |

---

## Anti-Patterns (NEVER)

- NEVER call `cursor_search` more than **2 times** per run
- NEVER call `cursor_search` to read a file whose path you already know — use `read`
- NEVER call `cursor_search` when ticket or anchors already name the target file
- NEVER keep re-reading the same files — read each relevant file once, then synthesize
- NEVER exceed the 8 primitive (read/ls/bash) call cap — emit the report instead
- NEVER guess file paths without read or search evidence
- NEVER leave a hypothesized file path out of `affected_files[]` — list every file that needs a fix
- NEVER use `read_file` for repository files (use `read` tool)
- NEVER generate patches or execution plans
