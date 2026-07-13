# `src/agents/` — Pipeline Agents

## Purpose

This directory contains the **four specialist agents** that do the real work on a Jira ticket: route the ticket, investigate the codebase, plan a fix, and apply it. Each agent is a Python module with a LangGraph **node** function that reads and updates shared `AgentState`.

Routing intelligence (what to do and when) lives in [`skills/`](../skills/) markdown files — not in Python. Python runners here are thin: load context, call LLM or Cursor, validate output, write state.

## How it fits in the pipeline

```
orchestrator → research → [Gate 1] → planner → [Gate 2] → executor
```

See the full flow in [`docs/AGENT_PIPELINE_GUIDE.md`](../../docs/AGENT_PIPELINE_GUIDE.md) and call trees in [`docs/project_structure.md`](../../docs/project_structure.md).

## Directory layout

```
src/agents/
├── orchestrator/     # Entry routing: Jira fetch, repo clone, FULL/MICRO/EXIT
├── research/         # Codebase investigation → ResearchReport
├── planner/          # Execution plan → ExecutionPlan (via Cursor Ask)
├── executor/         # Apply edits, validate, create PR
└── __init__.py
```

---

## `orchestrator/`

**Job:** First node on every run. Fetches the Jira ticket, resolves the GitHub repo, profiles the repo, and decides the pipeline route.

| File | Role |
|------|------|
| `agent.py` | `orchestrator_node()` — LangGraph entry; calls `run_orchestrator_routing()` |
| `tools/jira.py` | High-level Jira: `fetch_jira_ticket`, mock tickets, field parsing |
| `tools/jira_client.py` | Low-level Jira SDK wrapper |
| `tools/jira_schema.py` | `JiraTicketRaw` model, security enrichment |
| `__main__.py` | CLI: run routing standalone (dev/debug) |

**Brain:** Project LLM (`TaskComplexity.MEDIUM`) + [`skills/orchestrator/orchestrator.md`](../../skills/orchestrator/orchestrator.md)

**Output on state:** `routing_route` (`FULL` | `MICRO` | `EXIT`), `jira_ticket`, `repo_path`, `repo_profile`, `git_target_branch`

**How to read results:**
- `routing_route=FULL` → research runs next
- `routing_route=MICRO` → skips research, goes straight to planner
- `routing_route=EXIT` → ticket is Done/Resolved or not actionable

---

## `research/`

**Job:** Investigate the repo and produce a `ResearchReport`: hypotheses, affected files, architectural risk.

| File | Role |
|------|------|
| `agent.py` | `research_node()` — thin LangGraph wrapper |
| `research_agent.py` | `ResearchAgent` class — tool registry, invoke loop, report sanitization |
| `deep_runner.py` | Deepagents / ReAct harness, system prompt assembly |
| `middleware.py` | Progressive skill loading; gates `web_search` until `cursor_search` completes |
| `memory.py` | `InMemoryResearchMemory` — per-run research notes |
| `tools/builder.py` | Builds LangChain tool list (cursor_search, read, ls, bash, load_skill) |
| `tools/cursor_search.py` | LangChain wrapper for `CursorGateway.search()` |
| `tools/load_skill.py` | Loads skill bodies into agent context |
| `tools/file_discovery.py` | `extract_anchors()` — keyword hints from ticket text |
| `tools/repo_profile.py` | `profile_repository()`, `bootstrap_repo_context()` |
| `tools/web_search.py` | Optional external search |
| `sub_agent.py` | M1.3 alternate path (`CursorResearchSubAgent`) — tests only |
| `SEARCH_WORKFLOW.md` | How `cursor_search` → Cursor SDK works |
| `__main__.py` | CLI: run research standalone |

**Brain:** Project LLM (`TaskComplexity.HIGH`) for the tool loop; **Cursor** for `cursor_search` (max 2 calls per run)

**Output on state:** `research_output` — summary, `hypotheses[]`, `affected_files[]`, `flags[]`

**How to read results:**
- Check `affected_files[].path` — these feed the planner
- `flags` may include budget aborts, path remap warnings, or error markers
- On failure, research still returns a valid (empty) report so Gate 1 can review

**Related:** [`src/integrations/cursor/`](../../src/integrations/cursor/), [`skills/research/`](../../skills/research/)

---

## `planner/`

**Job:** Turn research into an ordered `ExecutionPlan` with READ → EDIT → VERIFY steps.

| File | Role |
|------|------|
| `agent.py` | `planner_node()` / `run_planner()` — Cursor ask + Python validation |
| `sub_agent.py` | M1.3 alternate path — tests only |
| `fixtures/mock_research_report.json` | Test fixture for unit tests |
| `__main__.py` | CLI: run planner standalone |

**Brain:** **Cursor Ask** (single call, read-only exploration) — not the project LLM. Prompt from [`skills/planner/planner.md`](../../skills/planner/planner.md).

**Output on state:** `plan_output` — `execution_steps[]`, `files_to_change[]`, `planner_snapshot_head`

**How to read results:**
- Each EDIT step has `instruction`, `intent_marker`, `target` (file path)
- `planner_snapshot_head` must match executor's git HEAD (consistency check)
- Validation failures raise `ValueError` → pipeline stops with `exit_message`

**Related:** [`src/integrations/cursor/semantic_plan_runner.py`](../../src/integrations/cursor/semantic_plan_runner.py), [`src/core/plan_validation.py`](../core/plan_validation.py)

---

## `executor/`

**Job:** Apply plan steps in a git worktree, validate edits, commit, and open a GitHub PR.

| File | Role |
|------|------|
| `agent.py` | `executor_node()` / `run_executor()` — step loop, worktree, PR |
| `validation.py` | Post-edit LLM validation (`validate_edit`) |
| `validation_context.py` | Snippet capture, semantic scope for validation |
| `structural_check.py` | Brace balance / `ast.parse` checks |
| `tools/builder.py` | Restricted ToolRegistry for read/bash |
| `tools/git_worktree.py` | Create/sync git worktree |
| `tools/mr_create.py` | GitHub PR creation |
| `fixtures/mock_plan.json` | Test fixture |
| `__main__.py` | CLI: run executor standalone |

**Brain:** **Cursor** for file edits (batched); project LLM (`TaskComplexity.LOW`) for post-edit validation only

**Output on state:** `execution_output` — `mr_url`, `changed_files`, `git_diff_summary`

**How to read results:**
- `mr_url` — link to the created PR (success)
- Failed steps → `save_failed_attempt()` in [`src/memory/`](../memory/) for retry context
- `step_results[]` in artifacts show READ/EDIT/VERIFY pass/fail per step

---

## Suggested reading order (new developers)

1. [`orchestrator/agent.py`](orchestrator/agent.py) — simplest agent, single LLM call
2. [`research/agent.py`](research/agent.py) → [`research_agent.py`](research/research_agent.py) → [`deep_runner.py`](research/deep_runner.py)
3. [`planner/agent.py`](planner/agent.py)
4. [`executor/agent.py`](executor/agent.py) → [`validation.py`](executor/validation.py)

Pair each agent with its skill file under [`skills/`](../../skills/).

## Related documentation

| Doc | Content |
|-----|---------|
| [`docs/AGENT_PIPELINE_GUIDE.md`](../../docs/AGENT_PIPELINE_GUIDE.md) | Plain-language pipeline walkthrough |
| [`docs/project_structure.md`](../../docs/project_structure.md) | Function call trees per agent |
| [`docs/CODEBASE_MAP.md`](../../docs/CODEBASE_MAP.md) | File-by-file classification |
| [`skills/`](../skills/) | Agent prompts and routing rules |
| [`AGENTS.md`](../../AGENTS.md) | Coding standards for AI assistants |
