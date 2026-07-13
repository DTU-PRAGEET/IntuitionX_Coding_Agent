# Skills Authoring Guide

> **Audience:** Teams extending the Jira Solver without editing Python in `src/core/` or `src/agents/`.  
> **Milestone:** 3.3 — Skills authoring (reference skills + this guide)  
> **Last updated:** 2026-07-13

This guide is the **primary artifact** for writing, testing, and publishing agent skills. If you follow it end-to-end, you do not need to read the rest of the codebase.

---

## Table of contents

1. [What is a skill?](#1-what-is-a-skill)
2. [Skill YAML schema reference](#2-skill-yaml-schema-reference)
3. [Writing trigger_conditions](#3-writing-trigger_conditions)
4. [Writing prompt_templates](#4-writing-prompt_templates)
5. [Declaring test_cases](#5-declaring-test_cases)
6. [Allowed file globs](#6-allowed-file-globs)
7. [Publishing a skill (PR checklist)](#7-publishing-a-skill-pr-checklist)
8. [Testing your skill](#8-testing-your-skill)
9. [Reference skills](#9-reference-skills)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. What is a skill?

A **skill** is a Markdown file under `skills/<module>/<skill-name>.md` that defines:

- When it activates (`trigger_conditions` / `trigger_labels`)
- What agents receive it (`agents`)
- How those agents should behave (`prompt_templates`, `implementation_guidance`)
- How to verify it (`test_cases`, `completion_criteria`)
- Which files a team may touch (`allowed_file_globs`)

At runtime, `SkillRegistry` in [`src/core/skill_manager.py`](../src/core/skill_manager.py) discovers skills and injects matched skills into agent prompts via `PromptBuilder` or the research `load_skill` tool. Malformed files log a warning and are skipped during discovery.

```
skills/
  orchestrator/orchestrator.md    ← routing (FULL / MICRO / EXIT)
  research/research.md            ← investigation workflow
  planner/planner.md              ← plan generation (Cursor Ask)
  executor/executor.md              ← edit validation policy
  security/<your-skill>.md        ← domain skills (your team adds here)
```

See also: [`skills/README.md`](../skills/README.md), [`src/core/README.md`](../src/core/README.md).

---

## 2. Skill YAML schema reference

Every skill **must** start with YAML frontmatter delimited by `---`:

```yaml
---
name: my-team-fixes              # required — unique across all skills/
module: security                 # required — directory under skills/
description: One-line summary    # required — shown in skill catalog
agents: [research, planner, implement]   # which agents may load this skill
fix_types: [parameterize_sql]    # optional — filter when ExecutionPlan has fix type
trigger_labels: security, sqli   # optional — Jira label / category triggers
trigger_languages: java          # optional — language hint in ticket text
tool_budget: 12                  # optional — max tool calls (default 10)
recursion_limit: 6               # optional — agent loop depth (default tool_budget/2)
allowed_file_globs:              # optional — team scope (see §6)
  - "src/auth/**"
  - "!src/auth/vendor/**"
---
```

### Field reference

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `name` | yes | string | Unique skill id; defaults to filename stem if omitted |
| `module` | yes | string | Subdirectory under `skills/` (e.g. `security`, `payments`) |
| `description` | yes | string | Short summary for logs and prompt headers |
| `agents` | recommended | list | `orchestrator`, `research`, `planner`, `implement` (alias for executor) |
| `fix_types` | optional | list | Canonical fix types from planner (`parameterize_sql`, `dependency_upgrade`, …) |
| `trigger_labels` | optional | list | Comma-separated or YAML list; matched against ticket `labels` and `vulnerability_category` |
| `trigger_languages` | optional | list | Matched against ticket title, problem statement, and labels |
| `tool_budget` | optional | int | 1–100; advisory limit encoded in skill |
| `allowed_file_globs` | optional | list | Paths this team's skill may edit (see §6) |

### Body sections (markdown `##` headings)

| Section | Purpose |
|---------|---------|
| `description` | Longer explanation of the skill's domain |
| `trigger_conditions` | Human-readable activation rules (mirrors frontmatter) |
| `prompt_templates` | Per-phase instructions (Research / Planner / Implement) |
| `implementation_guidance` | Fix patterns, framework notes, edge cases |
| `examples` | Before/after code snippets |
| `test_cases` | Documentation examples of scenarios the skill covers (see §5) |
| `allowed_file_globs` | Alternative to frontmatter globs (bullet list) |
| `completion_criteria` | Definition of done per pipeline phase |
| `references` | Links to external docs or `skills/*/references/` |

Unknown `##` sections are stored in the skill body but are not used by `format_for_prompt()` unless listed in its section allowlist.

---

## 3. Writing trigger_conditions

Skills activate when **all** of the following hold:

1. The agent is listed in `agents` (or `agents` is empty → all agents)
2. Ticket matches `trigger_labels` / `trigger_languages` (if declared)
3. If `fix_types` is set **and** an `ExecutionPlan` exists, the plan's fix type must match

### Label matching

`trigger_labels` are compared against:

- Jira `labels` (case-insensitive)
- `vulnerability_category` (tokenized — e.g. `injection` matches `sql-injection`)

Example — activates on SQLi tickets:

```yaml
trigger_labels: security, sqli, sql-injection, injection
```

### Language matching

Only evaluated when **no** label matched:

```yaml
trigger_languages: java, kotlin
```

Ticket problem statement `"Fix SQLi in UserDao.java"` → matches `java`.

### Security module default

Skills under `skills/security/` with **no** `trigger_labels` do **not** auto-activate (prevents every security skill loading on every ticket).

### Orchestrator recommendation

The orchestrator may set `recommended_security_skill` on the ticket. Research lists recommended skills in the user message; the agent calls `load_skill("<name>")` to pull the body into context.

### Example: narrow activation

```yaml
---
name: java-sast-fixes
module: security
trigger_labels: security, sast, sqli, xss
trigger_languages: java
agents: [research, planner, implement]
fix_types: [parameterize_sql, sanitize_input, xss_encoding]
---
```

---

## 4. Writing prompt_templates

`prompt_templates` override or extend agent behavior **without changing Python**. Each agent reads different parts:

| Agent | How skill content is delivered |
|-------|-------------------------------|
| **Orchestrator** | Full skill body + CONTEXT JSON → project LLM |
| **Research** | `load_skill` tool + progressive middleware injection |
| **Planner** | Full skill + CONTEXT → Cursor Ask (read-only) |
| **Executor** | `implementation_guidance` + domain skills → LLM validation prompt |

### Recommended structure

```markdown
## prompt_templates

### Research — Decision Flow
1. `cursor_search` for `<pattern>` from ticket
2. Read top hits; form hypothesis with file evidence
3. **Done when:** `affected_files` lists sink file(s)

### Planner — Decision Flow
1. Map each file to READ→EDIT→VERIFY triad
2. Use instruction-based EDIT steps (no raw patches)
3. **Done when:** `ExecutionPlan` JSON validates

### Implement — Decision Flow
1. Apply edits via batched Cursor call
2. VERIFY `intent_marker` in each edited file
3. **Done when:** all VERIFY steps pass
```

### Tips

- Write **imperative steps** — agents follow numbered flows reliably
- Include **Done when** criteria — reduces vague outputs
- Keep Research under **2 cursor_search** calls (hard budget in `research.md`)
- Planner must emit **instruction + intent_marker** on every EDIT step

---

## 5. Declaring test_cases

Use `## test_cases` to document **example scenarios** your skill is meant to handle. These are for human reviewers and skill authors — they are not executed automatically by the eval suite.

```markdown
## test_cases

- **sqli_parameterize**: DAO with string-concat SQL → parameterized query in patch
- **xss_encode_output**: Unencoded user input in HTML → context-appropriate encoding
- **skips_plain_bug**: Non-security tickets should not activate this skill
```

When testing selection manually, use a mock security ticket and dry-run research:

```bash
python3 -m src.agents.research --mock-security --repo-path . --dry-run
```

Check logs for `skill_selected` entries from `SkillRegistry`.

---

## 6. Allowed file globs

Teams can restrict which paths their skill's plans/edits may target:

```yaml
allowed_file_globs:
  - "src/payments/**"
  - "pom.xml"
  - "!**/test/**"
```

Or in the body:

```markdown
## allowed_file_globs

- `src/payments/**`
- `pom.xml`
- `!**/test/**`
```

### Glob rules

| Pattern | Meaning |
|---------|---------|
| `src/auth/**` | Allow any file under `src/auth/` |
| `pom.xml` | Allow exact file |
| `build.gradle*` | Allow `build.gradle` and `build.gradle.kts` |
| `!**/target/**` | Deny build output (negation with `!` prefix) |

Document team intent in the skill; executor scope enforcement is planned for Milestone 4.3.

---

## 7. Publishing a skill (PR checklist)

Before opening a PR:

- [ ] File lives at `skills/<module>/<name>.md` with valid `---` frontmatter
- [ ] `name` is unique across `skills/` (restart app or `SkillRegistry.reload()` to verify)
- [ ] `description` and `agents` are set
- [ ] `trigger_labels` are specific enough (does not fire on every ticket)
- [ ] `prompt_templates` include Research / Planner / Implement flows where applicable
- [ ] `test_cases` document at least one positive and one negative scenario
- [ ] `make evals-validate` passes (golden schema only)
- [ ] No secrets, customer code, or internal URLs in the skill body
- [ ] README updated if adding a new `skills/<module>/` directory

### PR description template

```markdown
## Skill: <name>

**Module:** security | payments | …
**Activates on:** labels X, Y; language Z
**Agents:** research, planner, implement

### Verification
- [ ] `python3 -m src.agents.research --dry-run` with mock security ticket
- [ ] `pytest tests/test_skill_registry.py -v`
```

---

## 8. Testing your skill

### Registry unit tests (no LLM / Cursor)

```bash
pytest tests/test_skill_registry.py -v
make evals-validate        # golden case schema only
```

### With agents (dry-run)

```bash
python3 -m src.agents.research --mock-security --repo-path . --dry-run
make evals                 # full integration benchmark (uses Cursor + judges)
```

### Interpret logs

| Log / signal | Meaning |
|--------------|---------|
| `skill_selected` | Skill matched ticket labels for that agent |
| `skill_load_failed` | Parse error — fix frontmatter or markdown structure |
| `skills_root_missing` | `skills/` path wrong or not deployed |

---

## 9. Reference skills

Three reference implementations ship with the repo — copy their structure:

| Skill | File | Stress-tests |
|-------|------|--------------|
| Java SAST | [`skills/security/java-sast-fixes.md`](../skills/security/java-sast-fixes.md) | SAST fix types, multi-phase templates |
| Java SCA | [`skills/security/java-sca-fixes.md`](../skills/security/java-sca-fixes.md) | CVE bumps, Maven/Gradle manifests |
| Java dependency vulns | [`skills/security/java-dependency-vulns.md`](../skills/security/java-dependency-vulns.md) | `allowed_file_globs`, BOM/transitive overrides |

---

## 10. Troubleshooting

| Problem | Fix |
|---------|-----|
| Skill not loading | Check logs for `skill_load_failed`; ensure `---` frontmatter and `name`/`description` |
| Skill loads but never selects | Add `trigger_labels`; verify ticket labels match |
| Duplicate skill name | Last file wins in registry — use unique `name` values |
| `fix_types` blocks selection on research | `fix_types` requires a matching `FixPlan`; research/planner only pass ticket context |

### Related documentation

| Doc | Content |
|-----|---------|
| [`skills/README.md`](../skills/README.md) | Skill directory layout |
| [`docs/AGENT_PIPELINE_GUIDE.md`](AGENT_PIPELINE_GUIDE.md) | How agents consume skills |
| [`evals/README.md`](../evals/README.md) | Full benchmark suite |
| [`evals/DOCS/eval_commands.md`](../evals/DOCS/eval_commands.md) | All eval commands |

---

**Definition of done (Milestone 3.3):** A developer unfamiliar with the codebase can write and test a new skill using **only this guide**.
