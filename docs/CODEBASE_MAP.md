# Codebase Map — Sprinklr Jira Solver

> Generated for: `chainlit run src/ui/chainlit_app.py --port 8080`  
> Last updated: 2026-06-25 — includes M1.3 Cursor integration + skills plugin layout.  
> This document classifies every tracked file so you can study the codebase efficiently.

---

## How the app runs

```mermaid
flowchart TD
    CLI["chainlit run src/ui/chainlit_app.py"]
    UI["src/ui/chainlit_app.py"]
    MOCK{"User input starts with `mock`?"}
    PIPE["src/pipeline/runner.py\n(linear, no gates)"]
    GRAPH["src/core/graph.py\n(LangGraph + HITL)"]
    ORCH["orchestrator_node"]
    RES["research_node → ResearchAgent → deep_runner"]
    CS["cursor_search → CursorGateway → cursor-sdk"]
    G1["research_hag (Gate 1)"]
    PLAN["planner_node"]
    G2["planning_hag / micro_confirm"]
    EXEC["executor_node\n(git worktree + MR)"]

    CLI --> UI
    UI --> MOCK
    MOCK -->|yes| PIPE
    MOCK -->|no| GRAPH
    PIPE --> ORCH
    GRAPH --> ORCH
    ORCH -->|FULL| RES
    ORCH -->|SKIP_RESEARCH / MICRO| PLAN
    ORCH -->|EXIT| END1[END]
    RES --> CS
    CS --> G1
    G1 -->|APPROVE| PLAN
    G1 -->|REVISE| RES
    G1 -->|REJECT| END2[END]
    PLAN --> G2
    G2 -->|APPROVE| EXEC
    G2 -->|REVISE| PLAN
    G2 -->|REJECT| END3[END]
    EXEC --> END4[END]
```

**Production research path:** `research_node` → `ResearchAgent` → `cursor_search` → `CliCursorGateway` → `run_local_agent()` in [`src/integrations/cursor/client.py`](src/integrations/cursor/client.py). Model: **`CURSOR_MODEL`** in `.env.local` (default `composer-2.5`).

**Alternate M1.3 path (not wired to Chainlit):** [`src/core/orchestrator_loop.py`](src/core/orchestrator_loop.py) + `src/agents/*/sub_agent.py` — tested only.

**Two user-facing paths:**

| Path | Trigger | Engine | Gates |
|------|---------|--------|-------|
| **Mock demo** | `mock SEC-4421` | `run_pipeline()` | None |
| **Production** | `PLAT-1234` | `build_graph().astream()` | Gate 1 (research), Gate 2 (plan), or micro-confirm |

On startup, Chainlit loads `chainlit_app.py`, which imports `build_graph()` — so **all four agent modules are imported immediately**, even before the user sends a message.

---

## 1. Read these first (in order)

Study the spine in this exact order:

| # | File | Why |
|---|------|-----|
| 1 | [`src/ui/chainlit_app.py`](src/ui/chainlit_app.py) | UI entry: parses input, routes mock vs HITL, streams progress |
| 2 | [`src/core/graph.py`](src/core/graph.py) | LangGraph wiring: nodes, gates, routing edges |
| 3 | [`src/core/state.py`](src/core/state.py) | `AgentState` schema passed between all nodes |
| 4 | [`src/core/schemas.py`](src/core/schemas.py) | Pydantic models: `ResearchReport`, `ExecutionPlan`, `RoutingDecision` |
| 5 | [`src/core/config.py`](src/core/config.py) | Env vars: LLM keys, Jira, **`CURSOR_MODEL`**, `WORKTREE_BASE` |
| 6 | [`src/agents/orchestrator/agent.py`](src/agents/orchestrator/agent.py) | Jira load, repo clone, LLM routing |
| 7 | [`src/core/ticket_repo.py`](src/core/ticket_repo.py) | Clones GitHub repos into `/tmp/worktrees/ticket_repos/` |
| 8 | [`src/agents/research/agent.py`](src/agents/research/agent.py) | Research LangGraph node (thin wrapper) |
| 9 | [`src/agents/research/research_agent.py`](src/agents/research/research_agent.py) | `ResearchAgent` class — invokes deep agent loop |
| 10 | [`src/agents/research/deep_runner.py`](src/agents/research/deep_runner.py) | Deepagents / ReAct harness, prompt assembly |
| 11 | [`src/agents/research/tools/cursor_search.py`](src/agents/research/tools/cursor_search.py) | LangChain tool wrapping `CursorGateway.search()` |
| 12 | [`src/integrations/cursor/gateway.py`](src/integrations/cursor/gateway.py) | CursorGateway + search JSON parsing |
| 13 | [`src/integrations/cursor/client.py`](src/integrations/cursor/client.py) | `run_local_agent()` — cursor-sdk, **`CURSOR_MODEL`** |
| 14 | [`src/agents/planner/agent.py`](src/agents/planner/agent.py) | Plan generation + verification |
| 15 | [`src/agents/executor/agent.py`](src/agents/executor/agent.py) | Step execution, worktree, PR creation |
| 16 | [`src/pipeline/runner.py`](src/pipeline/runner.py) | Linear mock path (bypasses LangGraph gates) |
| 17 | [`src/integrations/jira/client.py`](src/integrations/jira/client.py) | Jira fetch for UI preview |

**Prompt / routing intelligence** (read after the spine):

| File | Role |
|------|------|
| [`skills/orchestrator/orchestrator.md`](skills/orchestrator/orchestrator.md) | Orchestrator routing skill |
| [`skills/research/research.md`](skills/research/research.md) | Research workflow + tool budget |
| [`skills/planner/planner.md`](skills/planner/planner.md) | Planner system prompt |
| [`skills/executor/executor.md`](skills/executor/executor.md) | Executor system prompt |

**Skills plugin system:**

| File | Role |
|------|------|
| [`src/core/skill_manager.py`](src/core/skill_manager.py) | `SkillRegistry`, `PromptBuilder` — discovers repo-root `skills/` |

---

## 2. Safe to ignore for now

You do not need these to understand a normal `chainlit run` session.

### Tests
All files under [`tests/`](tests/) — unit/integration tests only.

### M1.3 scaffolding (imported by tests only — not Chainlit)
| File | Purpose |
|------|---------|
| [`src/core/orchestrator_loop.py`](src/core/orchestrator_loop.py) | Alternate orchestrator without LangGraph |
| [`src/core/context_builder.py`](src/core/context_builder.py) | Typed context models for M1.3 |
| [`src/core/state_updater.py`](src/core/state_updater.py) | Planned state mutation layer — **never imported** |
| [`src/core/transform_context.py`](src/core/transform_context.py) | Legacy context builder — **never imported** |
| [`src/core/agent_context.py`](src/core/agent_context.py) | `AgentContext` dataclass for sub-agents |
| [`src/core/jira_compact.py`](src/core/jira_compact.py) | Jira field compaction for context_builder |
| [`src/core/sub_agent.py`](src/core/sub_agent.py) | SubAgent ABC |
| [`src/agents/research/sub_agent.py`](src/agents/research/sub_agent.py) | CursorResearchSubAgent |
| [`src/agents/planner/sub_agent.py`](src/agents/planner/sub_agent.py) | CursorPlanSubAgent |
| [`src/agents/executor/sub_agent.py`](src/agents/executor/sub_agent.py) | CursorImplementSubAgent |
| [`src/integrations/cursor/plan_runner.py`](src/integrations/cursor/plan_runner.py) | Cursor-backed planner runner |
| [`src/integrations/cursor/implement_runner.py`](src/integrations/cursor/implement_runner.py) | Cursor-backed implement runner |
| [`src/integrations/cursor/research_runner.py`](src/integrations/cursor/research_runner.py) | Mock research output helpers (dry-run only) |
| [`src/integrations/cursor/poc.py`](src/integrations/cursor/poc.py) | Manual `make cursor-poc` smoke test |

### CLI entry points (not used by Chainlit)
| File | Purpose |
|------|---------|
| [`src/__main__.py`](src/__main__.py) | `python -m src` — tool registry smoke test |
| [`src/pipeline/__main__.py`](src/pipeline/__main__.py) | `python -m src.pipeline` — CLI pipeline runner |
| [`src/agents/research/__main__.py`](src/agents/research/__main__.py) | Standalone research runner |
| [`src/agents/planner/__main__.py`](src/agents/planner/__main__.py) | Standalone planner runner |
| [`src/agents/executor/__main__.py`](src/agents/executor/__main__.py) | Standalone executor runner |
| [`src/tools/cli.py`](src/tools/cli.py) | Tool demo CLI |

### Alternate server (FastAPI, not Chainlit)
| File | Purpose |
|------|---------|
| [`src/api/main.py`](src/api/main.py) | `uvicorn src.api.main:app` — REST API for CI/CD + approval resume |
| [`src/api/__init__.py`](src/api/__init__.py) | Package marker |

### Deployment / infra
| File | Purpose |
|------|---------|
| [`docker/Dockerfile`](docker/Dockerfile) | Container image (runs FastAPI by default) |
| [`docker/docker-compose.yml`](docker/docker-compose.yml) | Local stack (Mongo, Chainlit, API) |
| [`k8s/deployment.yaml`](k8s/deployment.yaml) | Kubernetes deployment |
| [`k8s/service.yaml`](k8s/service.yaml) | Kubernetes service |
| [`k8s/configmap.yaml`](k8s/configmap.yaml) | Kubernetes config |
| [`k8s/secrets.yaml`](k8s/secrets.yaml) | Kubernetes secrets template |

### Planning / docs / notes
| File | Purpose |
|------|---------|
| [`plan.md`](plan.md) | Internal implementation plan (1341 lines) |
| [`Implementation_plan.md`](Implementation_plan.md) | Earlier plan doc |
| [`Implementation_plan/plan_v2.md`](Implementation_plan/plan_v2.md) | Plan v2 |
| [`Project_state.md`](Project_state.md) | Project status notes |
| [`docs/PRD_ADDENDUM_PHASE1.md`](docs/PRD_ADDENDUM_PHASE1.md) | Product requirements addendum |
| [`AGENTS.md`](AGENTS.md) | Agent coding standards for AI assistants |
| [`chainlit.md`](chainlit.md) | Chainlit default welcome screen (unused — app sets its own welcome message) |

### Test fixtures (JSON)
| File | Purpose |
|------|---------|
| [`src/agents/planner/fixtures/mock_research_report.json`](src/agents/planner/fixtures/mock_research_report.json) | Used by `tests/test_planner.py` only |
| [`src/agents/executor/fixtures/mock_plan.json`](src/agents/executor/fixtures/mock_plan.json) | Used by `tests/test_executor.py` only |

### Reference / sample code (never imported)
| Path | Purpose |
|------|---------|
| [`pi sample tools/`](pi%20sample%20tools/) | TypeScript reference implementations of L1 tools |
| [`jira sample/`](jira%20sample/) | Early Jira client prototypes |

### Local / env / debug
| File | Purpose |
|------|---------|
| [`.env.local`](.env.local) | Local secrets (loaded by `config.py`) |
| [`.env.production`](.env.production) | Production env template |
| [`.cursor/debug-*.log`](.cursor/) | Cursor debug session logs |
| [`.gitignore`](.gitignore) | Git ignore rules |

### Root shim (not your entry point)
| File | Purpose |
|------|---------|
| [`chainlit_app.py`](chainlit_app.py) | `from src.ui.chainlit_app import *` — backward-compat re-export; docker-compose still mounts this |

---

## 3. Obsolete or deletable candidates

| File / path | Evidence | Recommendation |
|-------------|----------|----------------|
| [`src/core/state_updater.py`](src/core/state_updater.py) | **Zero imports** anywhere in repo | Delete or wire into orchestrator_loop |
| [`src/core/transform_context.py`](src/core/transform_context.py) | **Zero imports** — superseded by `context_builder.py` + `prompt_builder.py` | Delete |
| [`src/core/debug_log.py`](src/core/debug_log.py) | Only used by `sprinklr_router.py` error paths and orchestrator catch block | Keep for diagnostics or delete if Sprinklr tracing moves elsewhere |
| [`jira sample/jira_client.py`](jira%20sample/jira_client.py) | Superseded by [`src/agents/orchestrator/tools/jira_client.py`](src/agents/orchestrator/tools/jira_client.py); zero imports | Delete folder after confirming no external refs |
| [`jira sample/jira_schema.py`](jira%20sample/jira_schema.py) | Superseded by [`src/agents/orchestrator/tools/jira_schema.py`](src/agents/orchestrator/tools/jira_schema.py); zero imports | Delete with `jira sample/` |
| [`pi sample tools/*`](pi%20sample%20tools/) | TypeScript prototypes; Python tools live in `src/tools/`; zero imports | Archive or delete — useful only as design reference |
| [`chainlit_app.py`](chainlit_app.py) (root) | Duplicate entry; real app is `src/ui/chainlit_app.py`; only used by docker-compose command | Update docker-compose to use `src/ui/chainlit_app.py`, then delete shim |
| [`chainlit.md`](chainlit.md) | Default Chainlit boilerplate; app overrides welcome in `on_chat_start` | Delete or replace with project-specific content |
| [`.cursor/debug-*.log`](.cursor/) | Untracked debug artifacts | Add to `.gitignore`, delete locally |
| [`src/agents/research/skills/research/`](src/agents/research/skills/research/) | Duplicate of repo-root [`skills/research/`](skills/research/) — deep_runner uses `skills/` via `skill_registry` | Consolidate to one location |
| Agent `skills/scripts/*.py` | CLI helpers (`validate_plan.py`, `grep_symbols.py`, etc.) — run manually, not imported | Keep as dev utilities or move to `scripts/` |

**Not obsolete (common false positives):**

- [`src/api/main.py`](src/api/main.py) — separate deployment target (`uvicorn`), referenced in README and Dockerfile
- [`src/integrations/jira/client.py`](src/integrations/jira/client.py) — used by Chainlit for Jira preview; wraps orchestrator Jira helpers
- [`src/agents/orchestrator/tools/jira_client.py`](src/agents/orchestrator/tools/jira_client.py) — low-level Jira SDK client; used by `jira.py` and integrations client
- [`src/core/orchestrator_loop.py`](src/core/orchestrator_loop.py) — active M1.3 milestone code (test-covered), not dead — just not wired to Chainlit yet
- [`src/integrations/cursor/gateway.py`](src/integrations/cursor/gateway.py) — **production path** for all `cursor_search` calls

---

## 4. Full file classification

Legend: **CR** = Core Runtime · **RS** = Runtime Support · **CO** = Conditional Runtime · **DT** = Development / Testing · **UL** = Unused / Legacy

### `src/ui/` — Chainlit UI

| File | Cat | Description |
|------|-----|-------------|
| `src/ui/chainlit_app.py` | **CR** | Main UI: chat handlers, mock vs graph routing, HITL resume via `Command`, Jira preview |
| `src/ui/__init__.py` | **RS** | Package marker |

### `src/core/` — Shared kernel

| File | Cat | Description |
|------|-----|-------------|
| `src/core/graph.py` | **CR** | LangGraph `StateGraph`: orchestrator → research → gates → planner → executor |
| `src/core/state.py` | **CR** | `AgentState`, `initial_state()`, typed dicts for all pipeline artifacts |
| `src/core/schemas.py` | **CR** | Pydantic models: plans, reports, routing, tool inputs/outputs |
| `src/core/config.py` | **RS** | Loads `.env.local`; exposes `settings` (LLM keys, Jira, GitHub, paths) |
| `src/core/llm.py` | **RS** | `get_llm()` — model selection by task complexity |
| `src/core/logging.py` | **RS** | structlog setup (`configure_logging`, `get_logger`) |
| `src/core/runner_utils.py` | **RS** | `llm_content()`, `parse_llm_json()` — LLM response helpers |
| `src/core/formatters.py` | **RS** | Formats HITL gate messages (Gate 1, Gate 2, micro-confirm) |
| `src/core/checkpointing.py` | **RS** | Async SQLite / in-memory checkpointer for graph persistence |
| `src/core/skill_manager.py` | **RS** | `SkillRegistry`, `PromptBuilder` — repo-root `skills/` discovery |
| `src/core/transform_context.py` | **UL** | Legacy context builder — zero imports |
| `src/core/context_builder.py` | **CO** | M1.3 ContextBuilder — tests only |
| `src/core/state_updater.py` | **UL** | Planned artifact merger — zero imports |
| `src/core/agent_context.py` | **CO** | M1.3 typed context — orchestrator_loop tests only |
| `src/core/jira_compact.py` | **CO** | Jira compaction — context_builder tests only |
| `src/core/orchestrator_loop.py` | **CO** | M1.3 pipeline loop — tests only (not Chainlit) |
| `src/core/sub_agent.py` | **CO** | SubAgent ABC — M1.3 tests only |
| `src/core/skill_registry.py` | **RS** | Repo-root `skills/` discovery; deep_runner + M1.3 runners |
| `src/core/repo_snapshot.py` | **RS** | Git HEAD snapshot + edit preflight helpers |
| `src/core/plan_validation.py` | **RS** | Plan triad + edit patch validation |
| `src/core/prompt_builder.py` | **RS** | *(merged into `skill_manager.PromptBuilder`)* |
| `src/core/tracing.py` | **RS** | LangSmith metadata: `PROMPT_VERSIONS`, trace sync |
| `src/core/ticket_repo.py` | **RS** | Clones GitHub repo from Jira links when no local path given |
| `src/core/github_links.py` | **RS** | Parses GitHub URLs / slugs / target branches from ticket text |
| `src/core/tool_permissions.py` | **RS** | Reads `tool_permissions.yaml`; `get_allowed_tools(agent)` |
| `src/core/tool_permissions.yaml` | **RS** | Per-agent tool allowlists (loaded at runtime) |
| `src/core/debug_log.py` | **UL** | No-op `agent_debug()` stub — never imported |
| `src/core/__init__.py` | **RS** | Package marker |

### `src/agents/orchestrator/`

| File | Cat | Description |
|------|-----|-------------|
| `src/agents/orchestrator/agent.py` | **CR** | `orchestrator_node`: fetch ticket, resolve repo, LLM routing |
| `src/agents/orchestrator/skills/scripts.md` | **RS** | Routing prompt template (read at runtime) |
| `src/agents/orchestrator/tools/jira.py` | **RS** | High-level Jira: `fetch_jira_ticket`, mock ticket, field parsing |
| `src/agents/orchestrator/tools/jira_client.py` | **RS** | Jira SDK wrapper, credentials check |
| `src/agents/orchestrator/tools/jira_schema.py` | **RS** | `JiraTicketRaw` model, security enrichment |
| `src/agents/orchestrator/tools/__init__.py` | **RS** | Package marker |

### `src/agents/research/`

| File | Cat | Description |
|------|-----|-------------|
| `src/agents/research/agent.py` | **CR** | `research_node`: wraps `ResearchAgent.run()`, handles gate REVISE feedback |
| `src/agents/research/research_agent.py` | **CR** | `ResearchAgent` class — tool registry, deep agent invoke, report sanitize |
| `src/agents/research/deep_runner.py` | **CR** | Deep agent loop: harness profile, user message, JSON report output |
| `src/agents/research/sub_agent.py` | **CO** | `CursorResearchSubAgent` — M1.3 only |
| `src/agents/research/memory.py` | **RS** | `InMemoryResearchMemory` — per-run research notes |
| `src/agents/research/skills/research/SKILL.md` | **RS** | In-module research skill (deepagents backend reads repo-root `skills/research/`) |
| `src/agents/research/tools/builder.py` | **RS** | Builds LangChain tool list: cursor_search, web_search, repo_profile |
| `src/agents/research/tools/cursor_search.py` | **CR** | LangChain wrapper for `CursorGateway.search()` |
| `src/agents/research/tools/file_discovery.py` | **RS** | `extract_anchors()` — keyword hints from ticket text |
| `src/agents/research/tools/repo_profile.py` | **RS** | `profile_repository()` + `bootstrap_repo_context()` |
| `src/agents/research/tools/langchain_tools.py` | **RS** | `make_repo_profile_tool()` LangChain wrapper |
| `src/agents/research/tools/web_search.py` | **RS** | Optional web search LangChain tool |
| `src/agents/research/tools/__init__.py` | **RS** | Package marker |
| `src/agents/research/__main__.py` | **DT** | CLI: run research standalone |

### `src/agents/planner/`

| File | Cat | Description |
|------|-----|-------------|
| `src/agents/planner/agent.py` | **CR** | `planner_node` / `run_planner`: LLM plan + tool verification |
| `src/agents/planner/skills/scripts.md` | **RS** | Planner system prompt |
| `src/agents/planner/tools/builder.py` | **RS** | read/grep/find registry for planner |
| `src/agents/planner/fixtures/demo/UserSearchController.py` | **CO** | Mock-security demo target file (reset by mock pipeline) |
| `src/agents/planner/fixtures/mock_research_report.json` | **DT** | Test fixture for planner unit tests |
| `src/agents/planner/__main__.py` | **DT** | CLI: run planner standalone |

### `src/agents/executor/`

| File | Cat | Description |
|------|-----|-------------|
| `src/agents/executor/agent.py` | **CR** | `run_executor` + `executor_node`: steps, worktree, PR |
| `src/agents/executor/skills/scripts.md` | **RS** | Executor system prompt |
| `src/agents/executor/tools/builder.py` | **RS** | write/edit/bash tool registry |
| `src/agents/executor/tools/git_worktree.py` | **CO** | Git worktree create/commit/push — runs in `executor_node` only |
| `src/agents/executor/tools/mr_create.py` | **CO** | GitHub PR creation — runs after successful execution |
| `src/agents/executor/tools/__init__.py` | **RS** | Package marker |
| `src/agents/executor/fixtures/mock_plan.json` | **DT** | Test fixture for executor unit tests |
| `src/agents/executor/__main__.py` | **DT** | CLI: run executor standalone |

### `src/agents/`

| File | Cat | Description |
|------|-----|-------------|
| `src/agents/__init__.py` | **RS** | Package marker |

### `src/pipeline/`

| File | Cat | Description |
|------|-----|-------------|
| `src/pipeline/runner.py` | **CR** | `run_pipeline()` — mock/linear path; also imported by orchestrator for fixture reset |
| `src/pipeline/fixtures/user_search_controller_vulnerable.py` | **CO** | Source SQLi fixture copied into repo during `mock` runs |
| `src/pipeline/__init__.py` | **RS** | Package marker |
| `src/pipeline/__main__.py` | **DT** | CLI: `python -m src.pipeline TICKET /repo` |

### `src/tools/` — L1 primitives

| File | Cat | Description |
|------|-----|-------------|
| `src/tools/registry.py` | **RS** | `ToolRegistry` — registers all L1 tools for an agent |
| `src/tools/base.py` | **RS** | `ToolDefinition`, audit wrapper, event emitter (Chainlit hooks here) |
| `src/tools/read_tool.py` | **RS** | Read file with truncation |
| `src/tools/write_tool.py` | **RS** | Write file (executor) |
| `src/tools/edit_tool.py` | **RS** | Search-and-replace edit (executor) |
| `src/tools/edit_args.py` | **RS** | Normalizes edit tool arguments |
| `src/tools/edit_diff.py` | **RS** | Core diff/replace logic for edits |
| `src/tools/bash_tool.py` | **RS** | Sandboxed shell execution |
| `src/tools/grep_tool.py` | **RS** | Ripgrep-style search |
| `src/tools/find_tool.py` | **RS** | File discovery |
| `src/tools/ls_tool.py` | **RS** | Directory listing |
| `src/tools/path_utils.py` | **RS** | Path sandboxing / resolution |
| `src/tools/truncate.py` | **RS** | Output truncation for read/grep |
| `src/tools/cli.py` | **DT** | Interactive tool demo |
| `src/tools/__init__.py` | **RS** | Package marker |

### `src/integrations/cursor/` — Cursor SDK (production research path)

| File | Cat | Description |
|------|-----|-------------|
| `src/integrations/cursor/gateway.py` | **CR** | `CursorGateway` ABC, `CliCursorGateway`, search JSON parse |
| `src/integrations/cursor/client.py` | **CR** | `run_local_agent()` — cursor-sdk; model from `CURSOR_MODEL` |
| `src/integrations/cursor/schemas.py` | **RS** | `CursorSearchResponse`, `CursorEditResult` models |
| `src/integrations/cursor/plan_runner.py` | **CO** | M1.3 cursor-backed planner |
| `src/integrations/cursor/implement_runner.py` | **CO** | M1.3 cursor-backed implement |
| `src/integrations/cursor/research_runner.py` | **CO** | Mock research helpers for dry-run |
| `src/integrations/cursor/poc.py` | **DT** | Manual POC (`make cursor-poc`) |
| `src/integrations/cursor/__init__.py` | **RS** | Package marker |

### `skills/` — Repo-root skill plugins (read by `skill_registry`)

| File | Cat | Description |
|------|-----|-------------|
| `skills/research/research.md` | **RS** | Research workflow skill (deepagents reads via `/` virtual path) |
| `skills/research/references/security_patterns.md` | **CO** | Loaded for security tickets |
| `skills/planner/planner.md` | **RS** | Planner skill |
| `skills/executor/executor.md` | **RS** | Executor skill |
| `skills/orchestrator/orchestrator.md` | **RS** | Orchestrator routing skill |
| `skills/security/java-sast-fixes.md` | **CO** | Java SAST fix patterns (reference skill) |
| `skills/security/java-sca-fixes.md` | **CO** | Java SCA / CVE dependency fixes (reference skill) |
| `skills/security/java-dependency-vulns.md` | **CO** | Java dependency CVE / BOM / globs (reference skill, M3.3) |
| `docs/skills-authoring.md` | **DT** | Skills authoring guide for teams |

### `src/integrations/` (Jira)

### `src/memory/`

| File | Cat | Description |
|------|-----|-------------|
| `src/memory/cache.py` | **CO** | Failed-attempt persistence — read by orchestrator on retry; written on executor failure |
| `src/memory/__init__.py` | **RS** | Re-exports cache helpers |

### `src/api/` — FastAPI (separate entry)

| File | Cat | Description |
|------|-----|-------------|
| `src/api/main.py` | **DT** | REST API: `/health`, `/approve/{thread_id}`, CI/CD error ingestion |
| `src/api/__init__.py` | **DT** | Package marker |

### `src/` package root

| File | Cat | Description |
|------|-----|-------------|
| `src/__init__.py` | **RS** | Package marker |
| `src/__main__.py` | **DT** | Tool registry smoke test |

### `tests/`

| File | Cat | Description |
|------|-----|-------------|
| `tests/__init__.py` | **DT** | Package marker |
| `tests/test_core.py` | **DT** | Core module tests |
| `tests/test_pipeline.py` | **DT** | Pipeline integration tests |
| `tests/test_executor.py` | **DT** | Executor tests |
| `tests/test_planner.py` | **DT** | Planner tests |
| `tests/test_research.py` | **DT** | Research tests |
| `tests/test_tools.py` | **DT** | L1 tool tests |
| `tests/test_skills_loader.py` | **DT** | Skills loader tests |
| `tests/test_jira.py` | **DT** | Jira parsing tests |
| `tests/test_jira_client_parsed.py` | **DT** | Integrations Jira client tests |
| `tests/test_jira_issue_fields.py` | **DT** | Jira field extraction tests |
| `tests/test_jira_repo_links.py` | **DT** | GitHub link parsing tests |
| `tests/test_ticket_repo.py` | **DT** | Repo clone resolution tests |
| `tests/test_git_worktree.py` | **DT** | Git worktree tests |
| `tests/test_mr_create.py` | **DT** | MR creation tests |
| `tests/test_runner_utils.py` | **DT** | LLM helper tests |
| `tests/tools/test_edit_diff.py` | **DT** | Edit diff unit tests |

### Repository root & misc

| File | Cat | Description |
|------|-----|-------------|
| `chainlit_app.py` | **UL** | Backward-compat shim → `src/ui/chainlit_app.py` |
| `chainlit.md` | **UL** | Unused Chainlit welcome boilerplate |
| `requirements.txt` | **RS** | Python dependencies (loaded by pip, not Python import) |
| `README.md` | **DT** | Project documentation |
| `AGENTS.md` | **DT** | AI agent coding standards |
| `plan.md` | **DT** | Internal implementation plan |
| `Implementation_plan.md` | **DT** | Planning doc |
| `Implementation_plan/plan_v2.md` | **DT** | Planning doc v2 |
| `Project_state.md` | **DT** | Status notes |
| `docs/PRD_ADDENDUM_PHASE1.md` | **DT** | PRD addendum |
| `.env.local` | **RS** | Local config (read by `config.py` at import) |
| `.env.production` | **DT** | Production env template |
| `.gitignore` | **DT** | Git ignore rules |

### `docker/` & `k8s/`

| File | Cat | Description |
|------|-----|-------------|
| `docker/Dockerfile` | **DT** | Container build (FastAPI CMD) |
| `docker/docker-compose.yml` | **DT** | Local multi-service stack |
| `k8s/deployment.yaml` | **DT** | K8s deployment manifest |
| `k8s/service.yaml` | **DT** | K8s service manifest |
| `k8s/configmap.yaml` | **DT** | K8s config |
| `k8s/secrets.yaml` | **DT** | K8s secrets template |

### Legacy samples

| File | Cat | Description |
|------|-----|-------------|
| `jira sample/jira_client.py` | **UL** | Early Jira prototype — superseded |
| `jira sample/jira_schema.py` | **UL** | Early schema prototype — superseded |
| `pi sample tools/*.ts` (15 files) | **UL** | TypeScript tool reference implementations |

---

## 5. Conditional runtime — when each file executes

| File | Condition |
|------|-----------|
| `src/pipeline/runner.py` (`run_pipeline`) | User message starts with `mock` |
| `src/core/graph.py` gate nodes | Production path — pauses at Gate 1, Gate 2, or micro-confirm |
| `src/pipeline/fixtures/user_search_controller_vulnerable.py` | Mock path resets demo SQLi file into target repo |
| `src/agents/planner/fixtures/demo/UserSearchController.py` | Target file for mock security demo |
| `src/agents/research/skills/security.md` | Ticket has security label, security issue type, or CVE IDs |
| `src/agents/research/tools/web_search.py` | Research agent invokes web_search (allowed in permissions) |
| `src/agents/executor/tools/git_worktree.py` | `executor_node` runs (after plan approval) |
| `src/agents/executor/tools/mr_create.py` | Executor completes steps and creates GitHub PR |
| `src/memory/cache.py` (`get_failed_attempts`) | Orchestrator loads prior failures for same ticket |
| `src/memory/cache.py` (`save_failed_attempt`) | Executor step failure in graph path |
| `src/core/ticket_repo.py` | No explicit repo path — clones from Jira GitHub link |
| `src/core/checkpointing.py` (MongoDB) | `MONGO_URL` reachable (sync path only; Chainlit uses async SQLite/memory) |
| `src/agents/research/deep_runner.py` (deepagents) | `deepagents` package installed; else ReAct fallback |
| `src/integrations/cursor/client.py` | `CURSOR_API_KEY` set and research/planner calls `cursor_search` |
| `skills/research/references/security_patterns.md` | Ticket has security label, security issue type, or CVE IDs |

---

## 6. Quick stats

| Category | Count (approx.) |
|----------|-----------------|
| Core Runtime | ~18 Python files + skills `.md` |
| Runtime Support | ~55 Python files + YAML + env |
| Conditional Runtime | ~15 files (M1.3, cursor runners, security skills) |
| Development / Testing | ~45 files (tests + CLI + docs + k8s/docker) |
| Unused / Legacy | ~25 files (samples, shim, transform_context, state_updater) |

**Python files that matter for Chainlit:** ~60 under `src/` (excluding M1.3-only modules, `api/`, `__main__.py` files, and test-only fixtures).

---

## 7. Suggested study path

```
Day 1 — Spine (what happens when you type a ticket ID)
  chainlit_app.py → graph.py → state.py → schemas.py → config.py

Day 2 — Orchestrator + repo resolution
  orchestrator/agent.py → ticket_repo.py → integrations/jira/client.py
  skills/orchestrator/orchestrator.md

Day 3 — Research + Cursor (most complex path)
  research/agent.py → research_agent.py → deep_runner.py
  cursor_search.py → gateway.py → client.py
  skills/research/research.md

Day 4 — Planner + Executor
  planner/agent.py + skills/planner/planner.md
  executor/agent.py + skills/executor/executor.md
  executor/tools/git_worktree.py + mr_create.py

Day 5 — Tools & validation depth
  tools/registry.py + read/grep/find (planner)
  tools/write/edit/bash (executor)
  plan_validation.py, repo_snapshot.py, prompt_builder.py

Day 6 — Optional: M1.3 alternate architecture
  orchestrator_loop.py → sub_agent.py → */sub_agent.py
  context_builder.py, skill_registry.py
  tests/test_m13_sub_agents.py (executable spec)

Day 7 — Mock path + tests
  pipeline/runner.py (mock path)
  tests/test_pipeline.py, tests/test_research.py
```

### Files with zero imports in the Chainlit production path

These are never called when you run `chainlit run src/ui/chainlit_app.py`:

| File | Why it exists |
|------|---------------|
| `src/core/transform_context.py` | Superseded — safe to ignore |
| `src/core/state_updater.py` | Planned M1.3 — not wired |
| `src/core/orchestrator_loop.py` | M1.3 alternate entry — tests only |
| `src/core/context_builder.py` | M1.3 — tests only |
| `src/core/agent_context.py` | M1.3 — tests only |
| `src/core/jira_compact.py` | M1.3 — tests only |
| `src/agents/*/sub_agent.py` | M1.3 Cursor sub-agents — tests only |
| `src/integrations/cursor/poc.py` | Manual smoke test |
| `src/integrations/cursor/plan_runner.py` | M1.3 planner path |
| `src/integrations/cursor/implement_runner.py` | M1.3 implement path |
| `jira sample/*`, `pi sample tools/*` | Reference prototypes |
| `chainlit_app.py` (root) | Backward-compat shim |
| Agent `skills/scripts/*.py` | CLI utilities — run manually |
