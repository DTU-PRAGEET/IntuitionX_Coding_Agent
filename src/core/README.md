# `src/core/` — Shared Kernel

## Purpose

`src/core/` is the **spine** of the application. It defines shared state, schemas, the LangGraph workflow, LLM factory, skill loading, validation, and cross-cutting utilities used by all agents, the API, and the UI.

If you understand this directory, you understand how data flows between agents.

## Suggested reading order

Read these files in this exact order:

| # | File | Why |
|---|------|-----|
| 1 | `state.py` | `AgentState` — the shared dict every node reads/writes |
| 2 | `schemas.py` | Pydantic models: `ResearchReport`, `ExecutionPlan`, `RoutingDecision` |
| 3 | `graph.py` | LangGraph wiring: nodes, gates, routing edges |
| 4 | `config.py` | Env vars: LLM keys, Jira, `CURSOR_MODEL`, `WORKTREE_BASE` |
| 5 | `llm.py` | `get_llm()` — model selection by task complexity |
| 6 | `skill_manager.py` | `PromptBuilder`, `SkillRegistry` — loads `skills/` markdown |
| 7 | `plan_validation.py` | Plan triad + EDIT instruction validation |

Then branch into agent-specific context builders as needed.

## File layout

### Pipeline & state

| File | Role |
|------|------|
| `graph.py` | `build_graph()` — LangGraph `StateGraph`: orchestrator → research → gates → planner → executor |
| `state.py` | `AgentState`, `initial_state()`, typed dicts for all pipeline artifacts |
| `schemas.py` | Pydantic models for plans, reports, routing, tool I/O |
| `orchestrator_loop.py` | M1.3 alternate orchestrator (tests only — not production graph) |
| `sub_agent.py` | `SubAgent` ABC for M1.3 sub-agents |

### Context builders (prompt assembly)

| File | Role |
|------|------|
| `orchestrator_context.py` | Routing context JSON, `sanitize_routing_decision()`, user route override |
| `planner_context.py` | Research handoff + compact ticket for planner prompt |
| `agent_context.py` | `AgentContext` dataclass for M1.3 sub-agents |

### LLM & tracing

| File | Role |
|------|------|
| `llm.py` | `get_llm(TaskComplexity)` — provider selection (Sprinklr, Anthropic, OpenAI, etc.) |
| `runner_utils.py` | `llm_content()`, `parse_llm_json()` — LLM response helpers |
| `tracing.py` | LangSmith metadata, `invoke_tracked()` |
| `token_usage.py` | Per-agent token accounting |

### Skills & prompts

| File | Role |
|------|------|
| `skill_manager.py` | `SkillRegistry`, `PromptBuilder` — discovers and loads `skills/` |
| `tool_permissions.py` | Reads `tool_permissions.yaml`; `get_allowed_tools(agent)` |
| `tool_permissions.yaml` | Per-agent tool allowlists |

### Validation & repo helpers

| File | Role |
|------|------|
| `plan_validation.py` | READ→EDIT→VERIFY triad, instruction-based EDIT validation |
| `repo_snapshot.py` | Git HEAD snapshot, edit preflight helpers |
| `ticket_repo.py` | Clone GitHub repos from Jira links into cache |
| `github_links.py` | Parse GitHub URLs / slugs / target branches from ticket text |
| `file_paths.py` | Path sandboxing, Cursor temp-clone path repair |

### Infrastructure

| File | Role |
|------|------|
| `config.py` | Loads `.env.local`; exposes `settings` singleton |
| `logging.py` | structlog setup |
| `formatters.py` | HITL gate message formatting (Gate 1, Gate 2, micro-confirm) |
| `checkpointing.py` | Async SQLite / in-memory checkpointer for graph persistence |

### Skill scripts (deterministic helpers)

| Path | Role |
|------|------|
| `skill_scripts/read_file.py` | File read helper for skills |
| `skill_scripts/edit_file.py` | Edit helper |
| `skill_scripts/grep_symbols.py` | Symbol search |
| `skill_scripts/verify_edit.py` | Intent marker verification |
| `skill_scripts/run_tests.py` | Test runner hook |
| `skill_scripts/budget.py` | Tool budget tracking |
| `skill_scripts/file_index.py` | Repo file index |

## `AgentState` — the shared backpack

Every LangGraph node receives the full state and returns partial updates. Key fields:

| Field | Set by | Consumed by |
|-------|--------|-------------|
| `jira_ticket_id` | `initial_state()` | All agents |
| `jira_ticket` | Orchestrator | Research, Planner, Executor |
| `repo_path` | Orchestrator | Research, Planner, Executor |
| `routing_route` | Orchestrator | Graph routing |
| `research_output` | Research | Planner, Gate 1 |
| `plan_output` | Planner | Executor, Gate 2 |
| `execution_output` | Executor | UI / API |
| `human_gates` | Gate nodes | Re-run logic on REVISE |
| `previous_attempts` | Memory cache | Orchestrator, Planner |

Full schema: see `state.py`.

## How to interpret graph routing

`graph.py` defines conditional edges after each node:

```
orchestrator → FULL → research
             → MICRO / SKIP_RESEARCH → planner
             → EXIT → END

research_hag (Gate 1) → APPROVED → planner
                      → REVISE → research
                      → REJECTED → END

planning_hag (Gate 2) → APPROVED → executor
                      → REVISE → planner
                      → REJECTED → END
```

Gate nodes call LangGraph `interrupt()` — the graph pauses until the UI or API sends a resume `Command`.

## Two kinds of AI in this codebase

| System | Config | Used for |
|--------|--------|----------|
| **Project LLM** | `LLM_PROVIDER` + keys in `config.py` | Orchestrator routing, Research loop, Executor validation |
| **Cursor Agent** | `CURSOR_API_KEY` + `CURSOR_MODEL` | Research search, Planner plan, Executor edits |

These are independent — see [`docs/AGENT_PIPELINE_GUIDE.md`](../../docs/AGENT_PIPELINE_GUIDE.md) §10.

## Related documentation

| Doc | Content |
|-----|---------|
| [`docs/CODEBASE_MAP.md`](../../docs/CODEBASE_MAP.md) | Full file classification (CR/RS/CO/DT/UL) |
| [`docs/AGENT_PIPELINE_GUIDE.md`](../../docs/AGENT_PIPELINE_GUIDE.md) | Pipeline walkthrough with env vars |
| [`docs/DATA_FLOW_AUDIT.md`](../../docs/DATA_FLOW_AUDIT.md) | State field handoffs between agents |
| [`docs/project_structure.md`](../../docs/project_structure.md) | Function call trees |
| [`docs/skills-authoring.md`](../../docs/skills-authoring.md) | **Primary guide** for writing team skills |
| [`skills/README.md`](../../skills/README.md) | Skill plugin system loaded by `skill_manager.py` |
