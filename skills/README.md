# `skills/` — Agent Skill Plugins

## Purpose

This directory contains **markdown skill files** that define agent behavior, routing rules, tool budgets, and security remediation patterns. Python does not hard-code agent logic — it loads these files at runtime via `SkillRegistry` and `PromptBuilder` in [`src/core/skill_manager.py`](../src/core/skill_manager.py).

**Key principle:** To change how an agent thinks or routes, edit a `.md` file here and restart — no Python changes required.

**Authoring guide:** [`docs/skills-authoring.md`](../docs/skills-authoring.md) — write new skills without touching Python.

## How skills are loaded

```
Agent node runs
    → PromptBuilder.build("planner", SKILLS_DIR, context_json)
        → SkillRegistry discovers skills/<module>/<module>.md
        → Injects YAML frontmatter + body + runtime CONTEXT JSON
        → Sent to LLM or Cursor
```

Research uses progressive disclosure: the agent calls `load_skill` tool to pull in skill bodies on demand (see [`src/agents/research/tools/load_skill.py`](../src/agents/research/tools/load_skill.py)).

## Directory layout

```
skills/
├── orchestrator/
│   ├── orchestrator.md      # Routing skill (FULL / MICRO / EXIT)
│   └── README.md
├── research/
│   ├── research.md          # Investigation workflow + tool budget
│   ├── references/
│   │   ├── general_patterns.md
│   │   └── security_patterns.md
│   └── README.md
├── planner/
│   ├── planner.md           # Plan generation instructions
│   ├── references/
│   │   └── plan_schema.md   # ExecutionPlan field docs
│   └── README.md
├── executor/
│   ├── executor.md          # Validation policy + edit conventions
│   ├── references/
│   │   ├── validation_policy.md
│   │   └── edit_patterns.md
│   └── README.md
├── security/                # Domain skills — loaded on demand by vuln type
│   ├── injection.md
│   ├── broken-access-control.md
│   ├── cryptographic-failures.md
│   ├── authentication-failures.md
│   ├── java-sast-fixes.md
│   ├── java-sca-fixes.md
│   ├── java-dependency-vulns.md
│   └── ... (OWASP-aligned patterns)
└── README.md                # This file (team registry overview)
```

---

## Agent base skills

Each pipeline agent has one primary skill file:

| Module | File | Agent | Brain | Tool budget |
|--------|------|-------|-------|-------------|
| `orchestrator` | `orchestrator.md` | Orchestrator | Project LLM | 5 |
| `research` | `research.md` | Research | Project LLM + Cursor search | 12 |
| `planner` | `planner.md` | Planner | Cursor Ask | 8 |
| `executor` | `executor.md` | Executor | LLM (validation only) | 15 |

### How each skill is delivered

| Agent | Delivery method |
|-------|-----------------|
| Orchestrator | Full skill + CONTEXT JSON → single LLM call |
| Research | `load_skill` tool + system suffix; user message is CONTEXT only |
| Planner | Full skill + CONTEXT + schema → single Cursor ask |
| Executor | Body used as `validation_policy` for post-edit LLM checks |

---

## `orchestrator/orchestrator.md`

**Decides pipeline route before any code is read.**

| Route | Path |
|-------|------|
| `FULL` | Research → Gate 1 → Planner → Gate 2 → Executor |
| `MICRO` | Planner → Gate 2 → Executor (skips research) |
| `EXIT` | No downstream agents (ticket Done/Resolved) |

Explicit route requests in `user_prompt` override LLM routing (except hard EXIT stops).

**Output:** `RoutingDecision` JSON — `routing_route`, `risk_level`, `vulnerability_category`, `recommended_security_skill`

See [`orchestrator/README.md`](orchestrator/README.md)

---

## `research/research.md`

**Investigation workflow:** tool budget rules, hypothesis formation, `ResearchReport` JSON schema.

Key rules encoded:
- Call `load_skill` once for `research` + one security skill
- Max **2** `cursor_search` calls
- Max **8** read/ls/bash calls
- Emit `ResearchReport` when done

References loaded automatically for security tickets:
- `references/general_patterns.md` — always
- `references/security_patterns.md` — when security labels/CVE present

See [`research/README.md`](research/README.md)

---

## `planner/planner.md`

**Plan generation instructions** sent to Cursor Ask (read-only repo exploration).

Defines:
- `ExecutionPlan` JSON schema (READ → EDIT → VERIFY triad)
- Instruction-based EDIT steps (`instruction`, `intent_marker`, `approximate_line`)
- Path validation rules
- Domain skill summaries appended for matched security skills

Reference: [`planner/references/plan_schema.md`](planner/references/plan_schema.md)

See [`planner/README.md`](planner/README.md)

---

## `executor/executor.md`

**Validation policy** for post-edit LLM checks — not used for applying edits (those go through Cursor).

Defines:
- What constitutes a valid fix vs cosmetic change
- Regression risk criteria
- How to compare original vs modified snippets

References:
- [`executor/references/validation_policy.md`](executor/references/validation_policy.md)
- [`executor/references/edit_patterns.md`](executor/references/edit_patterns.md)

See [`executor/README.md`](executor/README.md)

---

## `security/` — Domain skills

OWASP-aligned remediation patterns loaded on demand:

| File | Vulnerability class |
|------|---------------------|
| `injection.md` | SQLi, command injection, LDAP |
| `broken-access-control.md` | IDOR, BAC, path traversal |
| `cryptographic-failures.md` | Weak crypto, hardcoded keys |
| `authentication-failures.md` | Auth bypass, session issues |
| `authentication-failures.md` | JWT, OAuth issues |
| `data-integrity-failures.md` | Deserialization, integrity |
| `security-misconfiguration.md` | Debug mode, default creds |
| `security-logging-failures.md` | Missing audit logs |
| `java-sast-fixes.md` | Java SAST remediation patterns |
| `java-sca-fixes.md` | Java SCA / CVE dependency fixes |
| `java-dependency-vulns.md` | Dependency CVE / BOM / globs (M3.3 reference) |
| `insecure-design.md` | Design-level flaws |
| `supply-chain-failures.md` | Dependency supply chain |
| `exceptional-conditions.md` | Error handling gaps |

**Selection:**
- Orchestrator sets `recommended_security_skill` on the ticket
- Research loads via `load_skill` tool
- Planner/executor get summaries via `format_domain_skills_for_prompt()`

See [`security/README.md`](security/README.md)

---

## Skill file format

Each `.md` skill has YAML frontmatter:

```yaml
---
name: injection
agents: [research, planner, executor]
trigger_conditions:
  vulnerability_category: [injection, sqli]
  labels: [security, sql-injection]
tool_budget: 12
---
```

Body sections follow the skill author's structure. Common sections: workflow steps, output schema, constraints, examples.

## Adding a new skill

1. Create `skills/<module>/<skill-name>.md` with YAML frontmatter
2. Set `agents: [research, planner, executor]` to control which agents receive it
3. Set `trigger_conditions` for automatic selection by ticket labels/category
4. Restart or call `SkillRegistry.reload()` — no Python changes required

## How to interpret skill changes in evals

When you change a skill, re-run integration evals to measure impact:

```bash
make evals   # full G-01–G-18 benchmark
```

Compare against [`evals/baselines/current.json`](../evals/baselines/current.json). Pass 3 (`run_030`, 100%) was achieved after planner/executor/research skill improvements — see [`evals/DOCS/failure-modes.md`](../evals/DOCS/failure-modes.md) §5.

## Related documentation

| Doc | Content |
|-----|---------|
| [`docs/AGENT_PIPELINE_GUIDE.md`](../docs/AGENT_PIPELINE_GUIDE.md) | §11 Skills & prompts reference |
| [`docs/example_skill(Gaurav).md`](../docs/example_skill(Gaurav).md) | Example skill authoring |
| [`src/core/skill_manager.py`](../src/core/skill_manager.py) | Loader implementation |
| [`evals/README.md`](../evals/README.md) | How to measure skill changes |
| [`AGENTS.md`](../AGENTS.md) | "Routing intelligence lives in skills/" |
