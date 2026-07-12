# Project Structure — Agent Function Call Trees

> **Purpose:** Document every major function call chain inside each pipeline agent, using the same ASCII tree style as the executor validation example.
>
> **Scope:** Production LangGraph path (`src/core/graph.py`). Sub-agent alternate paths (M1.3) are noted at the end.
>
> **Last updated:** 2026-07-11

---

## Table of contents

1. [Pipeline overview](#1-pipeline-overview)
2. [Orchestrator agent](#2-orchestrator-agent)
3. [Research agent](#3-research-agent)
4. [Planner agent](#4-planner-agent)
5. [Executor agent](#5-executor-agent)
6. [Shared integrations](#6-shared-integrations)
7. [Alternate sub-agent paths (M1.3)](#7-alternate-sub-agent-paths-m13)
8. [Source file index](#8-source-file-index)

---

## 1. Pipeline overview

Entry points: `build_graph()` in `src/core/graph.py`, driven by API (`src/pipeline/graph_runner.py`) or Chainlit UI.

```
build_graph()
    └── orchestrator_node()
            ├── route_after_orchestrator()
            │       ├── "FULL"      → research_node()
            │       ├── "MICRO"     → planner_node()        (skips research)
            │       ├── "SKIP_*"    → planner_node()
            │       └── "EXIT"      → END
            │
            ├── research_node()
            │       └── research_hag_node()                 ← HITL Gate 1 (interrupt)
            │               └── route_after_research_hag()
            │                       ├── APPROVED → planner_node()
            │                       ├── REVISE   → research_node()   (re-run)
            │                       └── REJECT   → END
            │
            ├── planner_node()
            │       └── planning_hag_node()                 ← HITL Gate 2 (interrupt)
            │               └── route_after_planning_hag()
            │                       ├── APPROVED → executor_node()
            │                       ├── REVISE   → planner_node()    (re-run)
            │                       └── REJECT   → END
            │
            └── executor_node()
                    └── END
```

**Legend used in all trees below:**

| Marker | Meaning |
|--------|---------|
| `← LLM HERE` | Project LLM call via `invoke_tracked()` / agent loop |
| `← CURSOR HERE` | Cursor SDK call via `run_local_agent()` |
| `← HITL` | Human-in-the-loop gate (`interrupt()`) |

---

## 2. Orchestrator agent

**Primary files:** `src/agents/orchestrator/agent.py`, `src/agents/orchestrator/tools/jira.py`, `src/core/orchestrator_context.py`

**LangGraph node:** `orchestrator_node()`

```
orchestrator_node(state)
    ├── get_failed_attempts(ticket_id)                    ← Redis/memory cache
    ├── fetch_jira_ticket(ticket_id)
    │       ├── credentials_configured()
    │       ├── create_jira_client()
    │       │       └── JIRA REST API (jira library)
    │       └── _issue_to_jira_ticket(issue, client)
    │               ├── enrich_security_ticket(raw)       ← security enrichment schema
    │               ├── _extract_vulnerability_category()
    │               ├── _extract_acceptance_criteria()
    │               ├── _compress_comments()
    │               ├── _extract_repo_links()
    │               └── _normalize_priority()
    │
    ├── is_ticket_done(ticket)                            ← early EXIT if Done/Resolved
    │
    ├── _resolve_repo_path(state)
    │       └── resolve_ticket_repo_path(ticket, explicit_path)
    │               ├── ticket_github_repo()
    │               ├── ticket_target_branch()
    │               └── ensure_github_checkout()            ← clone/sync to cache
    │
    ├── profile_repository(repo_path)                     ← RepoProfile for downstream agents
    │       └── (java_gradle | python | unknown detection)
    │
    ├── sync_trace_context_from_state(state)
    │
    └── run_orchestrator_routing(ticket, ...)             ← LLM HERE (single turn)
            ├── apply_trace_metadata()
            ├── get_llm(TaskComplexity.MEDIUM)
            ├── build_orchestrator_routing_context()
            │       ├── extract_named_file_paths()
            │       └── list_orchestrator_security_skills()
            ├── PromptBuilder.build("orchestrator", SKILLS_DIR, context)
            │       └── skills/orchestrator/orchestrator.md
            ├── invoke_tracked(llm, routing_prompt)       ← actual LLM API call
            ├── parse_llm_json(response, RoutingDecision)
            ├── sanitize_routing_decision()
            │       └── _sanitize_recommended_skill()
            ├── apply_locatability_micro_nudge()
            ├── apply_user_route_override()
            │       └── parse_explicit_user_route()
            └── apply_orchestrator_ticket_analysis(ticket, decision)
                    └── writes ticket_history_summary.orchestrator_analysis
```

**CLI entry (standalone):** `src/agents/orchestrator/__main__.py` → calls `run_orchestrator_routing()` directly without LangGraph.

**Output fields:** `routing_route`, `jira_ticket`, `repo_path`, `repo_profile`, `git_target_branch`, `risk_level`

---

## 3. Research agent

**Primary files:** `src/agents/research/agent.py`, `src/agents/research/research_agent.py`, `src/agents/research/deep_runner.py`, `src/agents/research/tools/*`, `src/agents/research/middleware.py`

**LangGraph node:** `research_node()`

```
research_node(state)
    ├── _latest_gate_feedback(state, "RESEARCH")          ← Gate 1 REVISE feedback
    ├── extract_anchors(ticket, user_prompt)              ← regex anchors from ticket text
    ├── InMemoryResearchMemory()
    │
    └── ResearchAgent(repo_path).run(ticket, ...)
            ├── profile_repository(repo_path)             ← if repo_profile missing
            ├── get_llm(TaskComplexity.HIGH)
            ├── load_skill_manifest_safe()
            │       └── load_skill_manifest(SKILL_DIR)
            ├── init_budget("research", thread_id, tool_budget)
            │
            ├── build_research_tool_registry(repo_path, gateway, ...)
            │       ├── make_load_skill_tool()
            │       │       └── load_skill(skill) closure
            │       │               └── _load_skill_body(skill)
            │       │                       ├── _normalize_skill_key()
            │       │                       ├── _resolve_skill_path()
            │       │                       ├── _read_skill_file()
            │       │                       └── _bundle_research_references()
            │       │
            │       ├── _make_primitive_lc_tools(repo_path, allowed)
            │       │       └── ToolRegistry.execute("read"|"ls"|"bash"|...)
            │       │               └── src/tools/* (read_tool, ls, bash, ...)
            │       │
            │       ├── make_cursor_search_tool(gateway, hard_limit=2)
            │       │       └── cursor_search(query, path) closure
            │       │
            │       ├── web_search                              (optional)
            │       └── make_repo_profile_tool(repo_path)       (optional)
            │
            ├── build_research_user_message(ticket, repo_path, ...)
            │       ├── bootstrap_repo_context(repo_path)
            │       ├── _compact_repo_profile()
            │       ├── _compact_security_enrichment()
            │       ├── memory.to_context_snippet()
            │       ├── _format_orchestrator_hints(ticket)
            │       └── _format_domain_skill_catalog(ticket)
            │               └── get_skill_registry().all_skills()
            │
            ├── build_research_agent(llm, tools, tool_budget)
            │       ├── _build_deep_agent()                   [preferred path]
            │       │       ├── _register_research_harness_profile()
            │       │       ├── create_deep_agent(model, tools, middleware, ...)
            │       │       │       ├── ProgressiveSkillMiddleware
            │       │       │       └── ResearchToolFilterMiddleware
            │       │       └── _research_system_prompt()
            │       │
            │       └── _build_react_fallback()               [if deepagents unavailable]
            │               └── create_react_agent(llm, tools, prompt)
            │
            └── agent.invoke({"messages": [...]}, config)     ← LLM HERE (multi-turn loop)
                    │
                    │   [Each agent iteration — model decides next tool]
                    │
                    ├── ProgressiveSkillMiddleware.wrap_model_call()
                    │       ├── _compact_load_skill_messages()
                    │       └── inject loaded_skills into system_message
                    │
                    ├── ResearchToolFilterMiddleware.wrap_model_call()
                    │       └── _visible_research_tools()       ← gates web_search until cursor_search done
                    │
                    ├── ProgressiveSkillMiddleware.wrap_tool_call()   [load_skill only]
                    │       └── handler → load_skill tool → inject skill body
                    │
                    └── Tool executions (chosen by LLM):
                            │
                            ├── load_skill(skill)
                            │       └── _load_skill_body(skill)
                            │
                            ├── cursor_search(query, path)            ← CURSOR HERE
                            │       ├── check_and_decrement(budget)
                            │       └── gateway.search(query, path)
                            │               └── CliCursorGateway._invoke_search()
                            │                       └── run_local_agent(prompt)   ← Cursor SDK
                            │
                            ├── read / ls / bash                        ← local ToolRegistry
                            │       └── _make_primitive_lc_tools budget gate (max 8 calls)
                            │
                            ├── web_search(query)                       ← optional
                            └── repo_profile()                          ← optional
                    │
                    └── [loop until ResearchReport emitted or recursion_limit]

            ├── record_messages_usage("research", messages)
            ├── _resolve_research_report(result)
            │       ├── result["structured_response"]           [deepagents structured output]
            │       ├── OR _extract_final_text(messages)
            │       └── parse_llm_json(text, ResearchReport)
            │
            └── _finalize_research_report(report, repo_path)
                    ├── repair_repo_from_cursor_temp_clone()
                    ├── _sanitize_research_report()
                    ├── _normalize_research_paths()
                    │       └── resolve_repo_relative_path()
                    └── _backfill_affected_files_from_hypotheses()
                            └── extract_repo_file_paths_from_texts()

    └── _report_to_output(report)                             ← ResearchOutput for AgentState
```

**On failure:** `_negative_finding_report()` returns a valid but empty `ResearchOutput` instead of crashing the graph.

**CLI entry:** `src/agents/research/__main__.py` → `run_research()` → `ResearchAgent.run()`

**Backward-compatible alias:**

```
run_research(ticket, repo_path, ...)
    └── ResearchAgent(repo_path).run(...)
```

---

## 4. Planner agent

**Primary files:** `src/agents/planner/agent.py`, `src/integrations/cursor/semantic_plan_runner.py`, `src/core/plan_validation.py`

**LangGraph node:** `planner_node()`

```
planner_node(state)
    ├── _latest_gate_feedback(state, "PLANNING")            ← Gate 2 REVISE feedback
    │
    ├── [MICRO route only] synthesize_micro_research_output(state)
    │       └── extract_repo_file_paths_from_texts(ticket text)
    │
    ├── ResearchReport.model_validate(research_raw)
    │
    └── run_planner(report, repo_path, ...)
            ├── repair_repo_from_cursor_temp_clone()
            ├── log_repo_snapshot("start", ...)
            ├── set_tracing_agent("planner")
            │
            ├── run_semantic_plan(...)                        ← CURSOR HERE (single ask)
            │   OR mock_semantic_plan(...)                    ← no CURSOR_API_KEY / dry_run
            │       │
            │       ├── build_semantic_plan_prompt(report, repo_path, ...)
            │       │       ├── research_handoff_for_planner(report)
            │       │       ├── compact_ticket_for_planner(ticket)
            │       │       ├── retry_summary_for_planner(previous_attempts)
            │       │       ├── PromptBuilder.build("planner", SKILLS_DIR, context)
            │       │       │       └── skills/planner/planner.md + references
            │       │       └── format_domain_skills_for_prompt("planner", ticket)
            │       │
            │       ├── gateway.ask(prompt)                   ← Cursor Ask mode
            │       │       └── CliCursorGateway.ask()
            │       │               └── run_local_agent(prompt)   ← Cursor SDK
            │       │
            │       └── parse_llm_json(run.text, ExecutionPlan)
            │
            ├── _normalize_plan_paths(plan, report, repo_path)
            │       ├── _remap_plan_path() for each step/file
            │       ├── sync_files_to_change_from_edit_steps()
            │       └── _validate_plan_executable()
            │               └── _is_valid_step_path()
            │
            ├── git_repo_snapshot(repo_path)
            │       └── plan.planner_snapshot_head = snap["head"]
            │
            ├── _log_affected_files(plan, repo_path)
            │
            ├── validate_instruction_edits(plan, repo_path)   ← hard errors on bad EDIT steps
            │
            ├── _validate_plan_files(plan, repo_path)         ← soft flags (file not found)
            │
            ├── validate_execution_plan(plan, repo_path, report)
            │       ├── validate_plan_triad()                 ← READ→EDIT→VERIFY per file
            │       └── validate_plan_paths()
            │
            ├── [append self_healing_notes from previous_attempts]
            │
            └── reset_tracing_agent()

    └── _plan_to_output(plan)                                 ← PlanOutput for AgentState
```

**CLI entry:** `src/agents/planner/__main__.py` → `run_planner()` / `load_research_report()`

**Output fields:** `plan_output` (ExecutionPlan as dict), `planner_snapshot_head` embedded for executor consistency check

---

## 5. Executor agent

**Primary files:** `src/agents/executor/agent.py`, `src/agents/executor/validation.py`, `src/agents/executor/validation_context.py`, `src/agents/executor/tools/*`

**LangGraph node:** `executor_node()`

### 5.1 Top-level executor node (worktree + PR)

```
executor_node(state)
    ├── ExecutionPlan.model_validate(plan_raw)
    │
    ├── create_worktree(repo_path, ticket_id, ...)
    │       ├── resolve_base_branch(repo_path, preferred)
    │       │       └── _remote_branch_exists()
    │       ├── make_branch_name(ticket_id, description)
    │       ├── [reuse existing worktree path if present]
    │       │       └── _sync_worktree_to_snapshot()
    │       │               ├── _worktree_is_dirty()
    │       │               ├── _start_ref(planner_head | origin/base)
    │       │               └── _reset_worktree(target_ref)
    │       ├── [or] git worktree add -b branch path start_ref
    │       └── _checkout_new_branch(worktree_path, branch_name)
    │
    ├── run_executor(plan, worktree_path, gateway, ticket)
    │       └── (see §5.2)
    │
    ├── show_diff(worktree_path, base_branch)
    ├── stage_commit_push(worktree_path, branch_name, commit_msg)
    │       └── git add / commit / push origin
    │
    └── create_github_pr(worktree_path, branch_name, ...)
            ├── resolve_github_repo()
            ├── _preflight_github_pr()
            └── _github_request("POST", "/repos/.../pulls")
```

**On failure:** `_save_failure()` → `save_failed_attempt()` to memory cache

---

### 5.2 Core execution engine

```
run_executor(plan, repo_path, ...)
    ├── configure_logging()
    ├── _normalize_executor_plan(plan, repo_path)
    │       ├── repair_repo_from_cursor_temp_clone()
    │       ├── remap_cursor_path() per step/file
    │       └── sync_files_to_change_from_edit_steps()
    │
    ├── log_repo_snapshot("start", ...)
    ├── _assert_snapshot_consistency(plan, repo_path)
    │       └── git_repo_snapshot() vs plan.planner_snapshot_head
    │
    ├── set_tracing_agent("executor")
    ├── _resolve_executor_llm(llm)
    │       └── get_llm(TaskComplexity.LOW)                 ← validation LLM tier
    │
    ├── load_validation_policy(ticket)
    │       ├── PromptBuilder.build_system_only(SKILLS_DIR)
    │       ├── append_references_for_agent(..., "validation_policy")
    │       └── format_domain_skills_for_prompt("executor", ticket)
    │
    ├── get_cursor_gateway(worktree_path)                   ← CliCursorGateway | LocalPatch | DryRun
    ├── build_executor_tools(repo_path)
    │       └── RestrictedToolRegistry(cwd, allowed=["read", "bash", ...])
    │
    ├── _run_edit_batch_phase(registry, plan, gateway, llm, ...)   ← all EDITs first
    │       └── (see §5.3)
    │
    ├── for each step in plan.execution_steps:
    │       ├── _assert_file_allowed(step, plan)
    │       └── _execute_step(registry, step, plan, edit_outcomes)
    │               ├── READ  → _execute_read()
    │               │               └── registry.execute("read", path, start_line, end_line)
    │               │
    │               ├── VERIFY → _execute_verify()
    │               │               ├── verify_intent_marker(cwd, path, marker)
    │               │               │       └── src/core/skill_scripts/verify_edit.py
    │               │               └── OR grep pattern checks
    │               │                       ├── _resolve_verify_patterns()
    │               │                       ├── _patterns_from_prior_edits()
    │               │                       └── _file_contains_fixed() / _file_contains_live_pattern()
    │               │
    │               ├── EDIT  → lookup edit_outcomes[step_id]     ← already applied in batch phase
    │               │
    │               └── RUN_TESTS → _execute_run_tests()
    │                               └── run_tests_for_file(cwd, path)
    │
    ├── ExecutionResult(changed_files, step_results, plan_deviations, ...)
    └── registry.dispose()
```

---

### 5.3 Edit batch phase (Cursor edits + LLM validation)

This is the structure from the user's example, expanded with full context:

```
_run_edit_batch_phase(registry, plan, gateway, llm, validation_policy, repo_path)
    ├── _edit_steps(plan)                                   ← filter action=="EDIT"
    │
    ├── for each edit step:
    │       ├── capture_pre_edit(repo_path, step)
    │       │       ├── pre_edit_span(step, lines, path)  ← validation_context.py
    │       │       └── read_snippet_at_span()
    │       └── file_content_hash(repo_path, path)          ← pre_hash
    │
    ├── _apply_batched_edits(gateway, plan, edit_steps, batch_pass=1)   ← CURSOR HERE
    │       ├── _build_batched_instruction(plan, steps)
    │       │       └── _build_edit_instruction(step, plan) per step
    │       ├── _semantic_edit_metadata(step, plan) per step
    │       └── gateway.edit(instruction, files, metadata)
    │               └── CliCursorGateway.edit()
    │                       └── run_local_agent(prompt)     ← Cursor SDK
    │
    ├── _validate_all_edits(..., batch_pass=1)              ← checks EACH edit step
    │       └── for each step:
    │               ├── capture_pre_edit() [if missing]
    │               ├── file_content_hash() [post_hash]
    │               ├── _evaluate_post_edit(...)            ← decides what checks to run
    │               │       │
    │               │       ├── [noop path] pre_hash == post_hash
    │               │       │       ├── _validation_snippets(DIFF_SEMANTIC)
    │               │       │       ├── validate_edit()                     ← LLM HERE (noop case)
    │               │       │       │       ├── _build_validation_prompt()
    │               │       │       │       │       ├── _validation_context()
    │               │       │       │       │       └── load_validation_policy snippets
    │               │       │       │       └── invoke_tracked(llm, prompt)   ← actual API call
    │               │       │       └── noop_failure_result() if LLM says pass but no diff
    │               │       │
    │               │       ├── [structural path]
    │               │       │       ├── _read_full_file()
    │               │       │       └── check_file_structure(path, content)
    │               │       │               └── is_python_hard_fail() → structural_failure_result()
    │               │       │
    │               │       └── validate_edit_progressive()                 ← normal case
    │               │               ├── level=DIFF_SEMANTIC
    │               │               │       ├── resolve_validation_snippets()
    │               │               │       │       ├── find_semantic_scope()
    │               │               │       │       └── semantic_scope_for_changed_lines()
    │               │               │       └── validate_edit()             ← LLM HERE (attempt 1)
    │               │               │               └── invoke_tracked(llm, prompt)
    │               │               │
    │               │               └── level=FULL_FILE [if first pass failed]
    │               │                       ├── warrants_context_expansion()
    │               │                       └── validate_edit()             ← LLM HERE (attempt 2)
    │               │                               └── invoke_tracked(llm, prompt)
    │               │
    │               ├── log_edit_outcome(...)
    │               ├── _outcome_from_result() [passed]
    │               └── _failed_from_result() [failed]
    │                       └── build_retry_note(prior ValidationResult)
    │
    ├── [if failures && max_passes >= 2] batch pass 2 repair
    │       ├── recapture pre_edit / pre_hash for failed steps
    │       ├── _apply_batched_edits(repair_steps, retry_notes, batch_pass=2)   ← CURSOR HERE
    │       └── _validate_all_edits(..., batch_pass=2, required_repair_step_ids)
    │
    └── _should_continue_after_validation_failure()
            ├── raise RuntimeError(_format_aggregate_edit_failure())   [hard fail]
            └── OR soft-fail → partial edits + PR with validation_warning
```

---

### 5.4 Validation context helpers

```
validation_context.py (used by capture_pre_edit / validate_edit_progressive)
    ├── pre_edit_span(step, lines, path)
    │       └── _approximate_window() from approximate_line
    │
    ├── resolve_validation_snippets(repo_path, step, pre_edit, level)
    │       ├── resolve_validation_span()
    │       │       ├── find_semantic_scope()
    │       │       │       ├── _python_semantic_scope()
    │       │       │       ├── _class_scope()
    │       │       │       └── _enclosing_brace_block()
    │       │       └── diff_changed_line_ranges()
    │       └── read_snippet_at_span() for original + modified
    │
    └── warrants_context_expansion(result, start, end, total_lines)
            └── _is_heavy_document_change() / _span_overlaps_file()
```

---

### 5.5 Structural check

```
structural_check.py
    └── check_file_structure(file_path, content)
            └── _check_brace_balance(content)
```

**CLI entry:** `src/agents/executor/__main__.py` → `run_executor()` / `load_execution_plan()`

---

## 6. Shared integrations

### 6.1 Cursor gateway factory

```
get_cursor_gateway(cwd, dry_run=False)
    ├── dry_run=True  → DryRunCursorGateway
    ├── CURSOR_API_KEY set → CliCursorGateway
    └── (executor fallback) → LocalPatchCursorGateway

CliCursorGateway
    ├── ask(prompt)     → _invoke()           → run_local_agent()   ← Cursor SDK
    ├── search(query)   → _invoke_search()   → run_local_agent()   ← Cursor SDK
    └── edit(instruction)                     → run_local_agent()   ← Cursor SDK
```

**File:** `src/integrations/cursor/gateway.py`, `src/integrations/cursor/client.py`

### 6.2 Skill / prompt loading

```
PromptBuilder.build(module, skills_dir, context, ticket)
    └── SkillRegistry + skills/<module>/<module>.md

Used by: orchestrator, planner (prompt), executor (validation_policy)
Research uses: load_skill tool + ProgressiveSkillMiddleware (not PromptBuilder for main loop)
```

**File:** `src/core/skill_manager.py`

### 6.3 Ticket repo resolution

```
resolve_ticket_repo_path(ticket, explicit_path)
    └── ensure_github_checkout(owner/repo, branch)
            └── git clone / fetch / checkout
```

**File:** `src/core/ticket_repo.py`

### 6.4 Human gates (not agents, but in call path)

```
research_hag_node(state)                          ← HITL Gate 1
    ├── format_gate1_request(state)
    ├── interrupt({gate: "GATE_1", ...})
    └── _record_gate(state, "RESEARCH", decision)

planning_hag_node(state)                          ← HITL Gate 2
    ├── format_gate2_request(state)
    ├── interrupt({gate: "GATE_2", ...})
    └── _record_gate(state, "PLANNING", decision)
```

**File:** `src/core/graph.py`

---

## 7. Alternate sub-agent paths (M1.3)

Not wired to Chainlit production graph; used by `src/core/orchestrator_loop.py` and tests.

### Research sub-agent

```
CursorResearchSubAgent.run(context)
    ├── extract_anchors(ticket, user_prompt)
    ├── profile_repository(repo_path)
    └── ResearchAgent(repo_path, gateway).run(...)
            └── (same tree as §3)
    └── report_to_research_output(report)
```

**File:** `src/agents/research/sub_agent.py`

### Planner sub-agent (legacy FixPlan path)

```
CursorPlanSubAgent.run(context)
    └── run_cursor_plan(research, repo_path, gateway)
            └── gateway.ask()                     ← CURSOR HERE
    └── fix_plan_to_plan_output(fix)
```

**File:** `src/agents/planner/sub_agent.py`, `src/integrations/cursor/plan_runner.py`

### Implement sub-agent (legacy patch path)

```
CursorImplementSubAgent.run(context)
    ├── SkillRegistry.select("implement", ticket, fix_plan)
    ├── run_cursor_implement(fix_plan, gateway)   ← CURSOR HERE (per-target edits)
    ├── [optional] create_worktree → stage_commit_push → create_github_pr
    └── PatchResult
```

**File:** `src/agents/executor/sub_agent.py`, `src/integrations/cursor/implement_runner.py`

---

## 8. Source file index

| Agent | LangGraph node | Main runner | Tools / integrations |
|-------|----------------|-------------|----------------------|
| **Orchestrator** | `orchestrator_node` | `run_orchestrator_routing` | `tools/jira.py`, `tools/jira_client.py`, `tools/jira_schema.py` |
| **Research** | `research_node` | `ResearchAgent.run` | `deep_runner.py`, `tools/builder.py`, `tools/cursor_search.py`, `tools/load_skill.py`, `middleware.py` |
| **Planner** | `planner_node` | `run_planner` | `semantic_plan_runner.py` (Cursor ask) |
| **Executor** | `executor_node` | `run_executor` | `validation.py`, `validation_context.py`, `tools/git_worktree.py`, `tools/mr_create.py`, `tools/builder.py` |

| Cross-cutting | Role |
|---------------|------|
| `src/core/graph.py` | LangGraph wiring + HITL gates |
| `src/core/state.py` | `AgentState` schema |
| `src/core/skill_manager.py` | Prompt / skill loading |
| `src/core/plan_validation.py` | Planner output validation |
| `src/core/orchestrator_context.py` | Routing context + post-processing |
| `src/integrations/cursor/gateway.py` | Cursor SDK wrapper |
| `src/core/ticket_repo.py` | GitHub clone for tickets |

---

## Related docs

- [AGENT_PIPELINE_GUIDE.md](./AGENT_PIPELINE_GUIDE.md) — plain-language pipeline walkthrough
- [CODEBASE_MAP.md](./CODEBASE_MAP.md) — file-by-file codebase classification
- [DATA_FLOW_AUDIT.md](./DATA_FLOW_AUDIT.md) — state field handoffs between agents
