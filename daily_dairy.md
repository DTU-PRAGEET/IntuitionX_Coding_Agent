# Daily Diary — Planner / Executor / Orchestrator Changes (Jul 4–12, 2026)

> **Period covered:** Friday Jul 4 → Saturday Jul 12, 2026  
> **Primary commits:** `2d0208f` (Jul 4) → `c60a004` (Jul 8) → `fb45b0f` (Jul 8)  
> **Key eval runs:** Pass 1 = `run_007` + `run_010` · Pass 2 = `mixed_15` + `mixed_3` · **Pass 3 = `run_030`**

---

## 1. Executive Summary

Over four days we moved the pipeline from **patch-based planning/execution** to **instruction-based semantic planning + LLM semantic post-edit validation**, built a full **LLM-as-Judge eval harness**, and fixed orchestrator routing semantics.

| Metric | Pass 1 (Jul 4) | Pass 2 (Jul 6) | Pass 3 (Jul 11) | Δ (P2→P3) |
|--------|----------------|----------------|-----------------|-----------|
| **End-to-end pass rate** | **3 / 18 (16.7%)** | **13 / 18 (72.2%)** | **18 / 18 (100%)** | **+27.8 pp** |
| `NO_OUTPUT` (planner killed run) | 6 | 0 | 0 | — |
| `WRONG_FIX_TYPE` | 5 | 0 | 0 | — |
| `BROKEN_SYNTAX` | 3 | 1 | 0 | −1 |
| `PARTIAL_FIX` | 1 | 4 | 0 | −4 |
| Planner judge mean | — | 77.4 | **94.9** | +17.5 |
| Executor judge mean | — | 75.9 | **96.8** | +20.9 |

**Biggest win (Pass 2):** Eliminating planner hard-fail semantic symbol resolution (`semantic_target.py` deleted in `c60a004`) — 6 cases that never reached the executor in Pass 1 now produce patches in Pass 2.

**Biggest win (Pass 3):** Skills improvements closed all 5 remaining failures — TypeScript (G-14, G-16), YAML (G-18), IDOR (G-12), and Misconfig (G-04) all recovered.

**Remaining caveat:** G-18 passes with executor score 70 — SealedSecret manifest files not visible in diff; soft failures documented.

---

## 2. Timeline of Work

| Date | What happened | Chat / commit |
|------|---------------|---------------|
| **Jul 4** | First integration eval (`run_007` + `run_010`). Eval framework redesign planned and implemented (LLM judges, artifacts, preflight). Planner → Cursor semantic plan; Executor → instruction + validation layer. | [Eval redesign](2c8b61db-6840-459c-bc9c-3ff44a9c94ca) · `2d0208f` |
| **Jul 6 AM** | Analyzed Pass 1 failures: planner `validate_semantic_edits` killing runs on recoverable ambiguity. Planned soft-validation pipeline v3. | [Soft validation plan](27b3e693-d795-41d6-b787-71444872f6d6) |
| **Jul 6 PM** | Implemented soft-validation v3 (`semantic_target.py`, warnings, density valve). Implemented executor two-phase batch redesign (always 2 Cursor batch passes max). | [Executor batch redesign](678c30fe-637f-45b8-a80b-5d9740655616) |
| **Jul 6 eve** | Ran Pass 2 evals: `mixed_15` (G-01–15) + `mixed_3` (G-15–18). Documented in `failure-modes.md`, promoted baseline. | `evals/baselines/current.json` |
| **Jul 8 AM** | **Architectural pivot:** Removed `semantic_target.py` (841 lines), replaced with **instruction-only validation** (no symbol resolution). Added `validation_context.py`, expanded executor skill. | `c60a004` |
| **Jul 8 PM** | Merged `SKIP_RESEARCH` → `MICRO`, added user-prompt route override, Gate 2 on MICRO path, `synthesize_micro_research_output`. | [MICRO merge](f444758c-29ca-4d19-ab4e-a3b7767fef42) |
| **Jul 8 eve** | API communication endpoints (execution events, pause display, Redis). | `fb45b0f` |
| **Jul 9** | Fixed MICRO routing eval harness (`repo_path` was null → forced FULL). Rejected `null repo_path → EXIT`. | [MICRO routing fix](80b39956-d8ee-4a32-8780-22a78ed6c278) |
| **Jul 11** | Ran Pass 3 eval (`run_030`, all 18 cases). Skills improvements: planner/executor/research skill updates. **18/18 pass (100%)**. Promoted baseline. | `evals/baselines/current.json` · `evals/runs/run_030/` |

---

## 3. Eval Results — Pass 1 vs Pass 2 vs Pass 3

### 3.1 Run configuration

| Run folder | Cases | Experiment name | `run_id` in metadata |
|------------|-------|-----------------|----------------------|
| `mixed_15_afterSemanticANDValidationChanges` | G-01 … G-15 | `mixed-15-unit-ChangingValidationLayerAndSemanticInstructions` | `run_026` |
| `mixed_3_afterSemanticANDVlidationChanges` | G-15 … G-18 | `mixed-18-unit-ChangingValidationLayerAndSemanticInstructions` | `run_025` |

Pass 1 baseline: `run_007` (8 cases) + `run_010` (10 cases), experiment `mixed-10-unit`.  
Pass 3 baseline: `run_030` (18 cases), experiment `Skills-adjustment-Test3`.

### 3.2 Per-case scorecard (all passes)

| Case | Pass 1 | Pass 2 | Pass 3 | Pass 2 failure mode | Notes |
|------|--------|--------|--------|---------------------|-------|
| G-01 | ✅ | ✅ | ✅ | — | Stable |
| G-02 | ❌ | ✅ | ✅ | — | bcrypt complete |
| G-03 | ❌ | ✅ | ✅ | — | `setup_jinja` resolved |
| G-04 | ❌ | ❌ | ✅ | `BROKEN_SYNTAX` | **Recovered P3** — config-driven debug/log_level |
| G-05 | ❌ | ✅ | ✅ | — | Selective POST-only auth |
| G-06 | ✅ | ✅ | ✅ | — | Stable |
| G-07 | ❌ | ✅ | ✅ | — | ORM fix accepted |
| G-08 | ❌ | ✅ | ✅ | — | `SECRET_KEY` env-sourced |
| G-09 | ❌ | ✅ | ✅ | — | Generic login error |
| G-10 | ❌ | ✅ | ✅ | — | PreparedStatement |
| G-11 | ❌ | ✅ | ✅ | — | JWT endpoint targeted |
| G-12 | ❌ | ❌ | ✅ | `PARTIAL_FIX` | **Recovered P3** — minimal IDOR guard |
| G-13 | ❌ | ✅ | ✅ | — | Path canonicalization |
| G-14 | ❌ | ❌ | ✅ | `PARTIAL_FIX` | **Recovered P3** — component + template XSS |
| G-15 | ❌ | ✅ | ✅ | — | Server-side `isAdmin` middleware |
| G-16 | ❌ | ❌ | ✅ | `PARTIAL_FIX` | **Recovered P3** — sync `jwt.verify` RS256 |
| G-17 | ❌ | ✅ | ✅ | — | Secret externalized |
| G-18 | ✅ | ❌ | ✅ | `PARTIAL_FIX` | **Recovered P3** — plain Secret removed (E70) |

### 3.3 Breakdown by dimension

| Dimension | Pass 1 | Pass 2 | Pass 3 (`run_030`) |
|-----------|--------|--------|---------------------|
| **Overall** | 16.7% | 72.2% | **100%** |
| **Python** | 22% (2/9) | 89% (8/9) | **100%** (9/9) |
| **Java** | 0% (0/5) | 80% (4/5) | **100%** (5/5) |
| **TypeScript** | 0% (0/3) | 33% (1/3) | **100%** (3/3) |
| **YAML** | 100% (1/1) | 0% (0/1) | **100%** (1/1) |
| **Injection** | 33% | 100% | **100%** |
| **Hard tickets** | 0% | 100% (2/2) | **100%** (2/2) |

### 3.4 mixed-15 vs mixed-3 split (executor judge only)

| Run | Pass | Fail | Failed cases |
|-----|------|------|--------------|
| mixed_15 | 12/14 | 2 | G-04 (VERIFY_FAIL), G-14 |
| mixed_3 | 1/3 | 2 | G-16, G-18 (G-17 passed) |

---

## 4. Changes You Accepted — and Why

### 4.1 Instruction-based planner (Cursor semantic plan)

| | |
|---|---|
| **Problem** | Old planner used in-process LLM + injected file contents + exact `old_string`/`new_string` patches. Patches were brittle (whitespace, multi-file, TypeScript/Angular). Eval Pass 1: planner semantic validation rejected 6/18 cases before executor ran. |
| **Changes** | `src/agents/planner/agent.py` rewritten: removed `build_planner_prompt`, `_affected_file_contents`, `MAX_VERIFICATION_CALLS`, planner tools loop. New path: single `run_semantic_plan()` / `mock_semantic_plan()` via `src/integrations/cursor/semantic_plan_runner.py`. EDIT steps use `instruction`, `intent_marker`, `target_type`, `target_name`, optional `approximate_line`, `caution_flag`. `prompt_version` → `planner-v2.0-semantic`. |
| **Reason accepted** | Cursor explores the repo at plan time (better grounding than regex symbol resolution). Natural-language instructions survive formatting drift. Aligns with executor's new validation model. |

### 4.2 Remove hard symbol resolution (`semantic_target.py` deleted)

| | |
|---|---|
| **Problem** | `validate_semantic_edits()` in `semantic_target.py` (841 lines) raised `ValueError` on recoverable cases: duplicate `this.searchValue`, wrong `target_type` for methods, `__main__` as function, import ambiguity. **40% of Pass 1 failures were `NO_OUTPUT`.** |
| **Changes** | Deleted `src/core/semantic_target.py` in `c60a004`. `src/core/plan_validation.py` now has `validate_instruction_edits()` — checks instruction + intent_marker + target_type/name present; rejects legacy `old_string`/`new_string`; **no AST/regex symbol resolution**. Added `src/core/edit_step.py` (`is_instruction_edit`). Triad/path checks remain as soft flags via `validate_execution_plan()`. |
| **Reason accepted** | Soft-validation v3 (Jul 6) was still too complex; symbol resolver false-negatives blocked good plans. Instruction + `caution_flag` + executor LLM validation is simpler and empirically +55.5 pp. |

### 4.3 Executor LLM semantic validation + progressive context

| | |
|---|---|
| **Problem** | Old executor validated via exact `old_string` match and substring checks. Incomplete Cursor edits (truncated bcrypt, imports-only JWT) passed or failed arbitrarily. Per-file retry loop was inconsistent with batching. |
| **Changes** | New modules: `src/agents/executor/validation.py` (643 lines), `validation_context.py` (452 lines), `structural_check.py` (89 lines). Flow: noop check → Python `ast.parse` hard fail → brace warnings (TS/JS) → LLM validator with diff-expanded semantic parent → full-file fallback → batch pass #2 repair. `skills/executor/executor.md` defines acceptance/rejection/non-error policy. `CURSOR_MAX_EDIT_ATTEMPTS=2` (whole-plan batch passes). |
| **Reason accepted** | Validates **security property** in instruction scope, not patch shape. Reduces false fails on equivalent implementations (`${ENV}` vs `os.getenv`, etc.). Progressive context fixes "validator only saw declaration header" bug. |

### 4.4 Executor always batch ALL edits (two-phase batch redesign)

| | |
|---|---|
| **Problem** | `_independent_edits()` gated batching; chained same-file plans fell back to per-file loop. Retry budget uneven; batched repair bug (`skip_initial_cursor`) meant pass 2 often didn't re-call Cursor. |
| **Changes** | Removed `_independent_edits`, `_execute_edit`, `_apply_edit_via_cursor`, `_accept_batched_edit`. `_run_edit_batch_phase()`: pass #1 all EDITs → validate all → pass #2 failed steps only. `_apply_batched_edits(steps, retry_notes, batch_pass)`. `FailedEditStep`, `EditStepOutcome` dataclasses. Aggregate failure message lists all failing steps. |
| **Reason accepted** | Predictable Cursor budget (max 2 calls). Consistent behavior for multi-file plans. Repair actually re-invokes Cursor with `missing_requirements` from validator. |

### 4.5 Planner skill: instruction contract + caution_flag

| | |
|---|---|
| **Problem** | Vague planner instructions → incomplete Cursor edits on retry. Ambiguous symbols caused hard fails. |
| **Changes** | `skills/planner/planner.md`: Instruction Contract (what to change / what NOT to touch / inline Acceptance checklist). Semantic Targeting (`target_type`, `target_name`). `caution_flag` for duplicate symbols (guidance, not error). VERIFY `intent_marker` must be literal code token. Anti-pattern: never target `__main__` as function. `skills/planner/references/plan_schema.md` updated with instruction-based examples. |
| **Reason accepted** | Acceptance checklist in instruction gives Cursor self-verify targets. `caution_flag` disambiguates without blocking pipeline. |

### 4.6 Eval framework redesign (LLM-as-Judge)

| | |
|---|---|
| **Problem** | Old evals couldn't score semantic plans or instruction-based executor output. No artifact capture for debugging. |
| **Changes** | `evals/artifacts/pipeline_result.py`, `evals/harness/artifact_capture.py`, `evals/evaluators/planner_judge.py`, `executor_judge.py`, `llm_judge_base.py`, `preflight.py`. `evals/runners/run_judge_eval.py`, `compare_runs.py`. `evals/DOCS/evaluator_architecture.md`, `failure-modes.md`. Fatal vs warning preflight (invalid JSON = skip judge; `match_count=0` = still judge with warning context). Tiered judge models. `evals/baselines/current.json` promoted from Pass 2. |
| **Reason accepted** | Hybrid deterministic + LLM judge matches new architecture. Artifacts enable per-case debugging (frozen in `evals/runs/<run_id>/`). Your feedback incorporated: don't skip judge on resolver warnings; add RootCauseAnalysis + RegressionRisk criteria. |

### 4.7 Orchestrator: locatability / risk_floor decoupling

| | |
|---|---|
| **Problem** | Security labels (`auth`, `sqli`, etc.) forced **FULL** for all security tickets, including self-locating single-file fixes (G-04, G-06–G-09, G-17, G-18). |
| **Changes** | `skills/orchestrator/orchestrator.md` Step 3: security labels set `risk_floor=HIGH` only, do NOT force route. Step 4: locatability check (file named in ticket?) gates MICRO vs FULL. Step 5b: orchestrator classifies `vulnerability_category` + `recommended_security_skill`. `RoutingDecision` schema extended. `apply_orchestrator_ticket_analysis()` writes classification back to ticket. |
| **Reason accepted** | HIGH risk ≠ needs research. Many CVE fixes are one named file + bounded change. |

### 4.8 Merge SKIP_RESEARCH → MICRO + Gate 2 on MICRO

| | |
|---|---|
| **Problem** | `SKIP_RESEARCH` and `MICRO` both skipped research → planner; duplicate route confused evals and graph. MICRO path skipped Gate 2 (plan approval). |
| **Changes** | `RoutingDecision` literal type: `FULL | MICRO | EXIT` only; `@field_validator` coerces `SKIP_RESEARCH` → `MICRO`. `src/core/graph.py`: MICRO → `planning_hag` (Gate 2), removed `micro_confirm`. `synthesize_micro_research_output()` in planner when no research on MICRO. `evals/evaluators/scoring.py` `_norm()` treats SKIP_RESEARCH ≡ MICRO. User-prompt route override: Step 3.5 in skill + `apply_user_route_override()` in Python. |
| **Reason accepted** | You explicitly requested merge + Gate 2 on MICRO. Simpler mental model; plan still human-reviewed before executor. |

### 4.9 MICRO routing harness fix (Jul 9)

| | |
|---|---|
| **Problem** | Eval harness called `resolve_routing()` without `repo_path`. Orchestrator skill Step 4b: null `repo_path` → FULL hard stop. All 7 MICRO golden cases routed FULL despite correct LLM reasoning. |
| **Changes** | `evals/harness/targets.py`: `_routing_repo_context()`, `_is_micro_expected_route()`. `src/pipeline/runner.py`: `resolve_routing(..., repo_path=, repo_profile=)`. Test: `test_routing_repo_context_only_for_micro_cases`. |
| **Reason accepted** | Production `orchestrator_node` resolves repo before routing; evals must mirror that. LLM was correct; context was wrong. |

### 4.10 Supporting infrastructure (accepted as part of the arc)

| Area | Changes |
|------|---------|
| **skill_manager** | Replaced `skill_registry.py` + `skills_loader.py` with unified `src/core/skill_manager.py` (`PromptBuilder`). |
| **file_paths** | `remap_cursor_path`, `repair_repo_from_cursor_temp_clone` for Cursor temp clone paths. |
| **schemas** | `ExecutionStep`: `instruction`, `target_type`, `target_name`, `caution_flag`; legacy patch fields deprecated. `ExecutionResult.validation_failed_steps`. |
| **config** | `cursor_max_edit_attempts`, `SEMANTIC_SOFT_VALIDATION_ENABLED`, `semantic_warning_density_threshold`. |
| **tests** | `test_executor_validation.py`, `test_validation_context.py`, `test_executor_structural_validation.py`, `test_instruction_edits.py`, `test_executor_batch_flow.py`, expanded `test_planner.py` / `test_executor.py`. |
| **docs** | `docs/AGENT_PIPELINE_GUIDE.md` (706 lines), `evals/DOCS/eval_commands.md`, repo testing docs for 12 vulnerable repos. |
| **API** (`fb45b0f`) | `execution_events.py`, `pause_display.py`, `redis_store.py`, `API_GUIDE.md` — real-time pipeline visibility. |

---

## 5. Changes You Rejected or Deferred — and Why

### 5.1 `null repo_path` → EXIT (rejected Jul 9)

| | |
|---|---|
| **Proposal** | If `repository.repo_path` is null, orchestrator should EXIT and stop pipeline. |
| **Decision** | **Rejected.** Null `repo_path` means "local checkout not resolved yet," not "invalid ticket." Tickets can have `repo_links` (GitHub URL) while checkout is pending → route **FULL** (repo bootstrap), not EXIT. EXIT is reserved for Done tickets, out-of-scope features, budget exhaustion. |
| **What we did instead** | Fixed eval harness to pass `repo_path` for MICRO cases; kept FULL hard stop when `repo_path` is null (blocks MICRO only). |

### 5.2 Bounded planner re-plan on density valve (deferred to v2)

| | |
|---|---|
| **Proposal** | When >50% EDIT steps have unresolved semantic targets, re-invoke planner instead of hard stop. |
| **Decision** | **Deferred.** v1 = intentional hard stop with instrumentation (`planner_semantic_density` event). Re-plan risks repeating same mistakes at extra cost. Flag `PLANNER_SEMANTIC_REPLAN_ENABLED` reserved for v2. |
| **What happened later** | Entire `semantic_target.py` removed in `c60a004`, making density valve moot — replaced by instruction-only validation. |

### 5.3 Executor as free-form skill runner (rejected Jul 8)

| | |
|---|---|
| **Proposal** | Drop `docs/example_skill(Gaurav).md` into executor and have it raise MR via `glab` without Python orchestration. |
| **Decision** | **Rejected.** Pipeline executor is a **deterministic plan runner**, not a ReAct agent. It only uses `skills/executor/executor.md` as validation policy. MR creation stays in `mr_create.py` / hardcoded git phase. |
| **Reason** | Architecture separation: skills guide LLM reasoning; Python enforces step types, budgets, and gates. |

### 5.4 Replace PromptBuilder with raw f-string skill injection (rejected Jul 8)

| | |
|---|---|
| **Proposal** | Pass skill files directly as f-strings to Cursor instead of `PromptBuilder.build()`. |
| **Decision** | **Rejected** (advised to keep PromptBuilder). `PromptBuilder` handles manifest, context JSON injection, domain skill formatting, reference loading consistently across orchestrator/research/planner/executor. |
| **Reason** | Centralized assembly prevents drift; changing one agent's prompt pattern doesn't break others. |

### 5.5 Raw Jira ticket to LLM without parsing layer (rejected / not adopted Jul 9)

| | |
|---|---|
| **Proposal** | Give full Jira ticket to LLM and let it extract repo link, vuln class, files — skip Python parsing. |
| **Decision** | **Not adopted.** Kept `build_orchestrator_routing_context()`, `jira.py` fetch, `ticket_repo.py` resolution. |
| **Reason** | Deterministic integration gates (DONE status, repo bootstrap, eval reproducibility) need structured fields. LLM routing still runs on **compact JSON context**, not raw ticket blob. |

### 5.6 Planner dependency graph in prompt (requested Jul 8 — not implemented)

| | |
|---|---|
| **Proposal** | Add explicit dependency ordering in planner prompt for executor (e.g. "edit config schema before app.py"). |
| **Decision** | **Analysis only; not implemented.** Current plan uses sequential `step_id` READ→EDIT→VERIFY blocks per hypothesis. Executor runs steps in order; no separate dependency graph structure. |
| **Reason** | READ→EDIT→VERIFY triad per file already implies order. Cross-file deps could be a future planner skill addition when multi-file ordering failures appear in evals. |

### 5.7 `user_prompt` as routing control before Jul 8 (implicit rejection)

| | |
|---|---|
| **Problem** | User typed "MICRO route" in API `user_prompt`; pipeline still went FULL → research. |
| **Decision** | Initially **not wired** — `user_prompt` only affected research search queries. **Fixed Jul 8** with explicit Step 3.5 + `apply_user_route_override()` after you requested it. |

### 5.8 Keep `semantic_target.py` soft-validation path (superseded Jul 8)

| | |
|---|---|
| **What happened** | Jul 6: implemented full soft-validation v3 with warnings, `resolve_effective_target`, density valve, instruction contract in executor. Jul 8 (`c60a004`): **removed entire module** in favor of instruction-only checks. |
| **Reason for pivot** | Pass 2 eval proved instruction-based flow works without resolver. Maintaining 841-line regex resolver had high false-negative cost. Simpler `validate_instruction_edits()` + executor LLM validation achieved 72.2% without it. |

---

## 6. Planner Architecture — Deep Dive

### 6.1 Old architecture (pre Jul 4)

```
Research → Python LLM planner (tools: read, verify)
         → inject AFFECTED FILE CONTENTS into prompt
         → output old_string/new_string patches
         → validate_edit_patches() + validate_semantic_edits()
         → Executor applies exact patches
```

### 6.2 New architecture (current)

```
Research (FULL) or synthesize_micro_research_output (MICRO)
         → Cursor Ask: run_semantic_plan() — explores repo on disk
         → Python: schema validate + validate_instruction_edits + triad/path flags
         → normalize paths (remap_cursor_path, sync files_to_change)
         → Gate 2 (human)
         → Executor
```

### 6.3 Key planner files changed

| File | Change |
|------|--------|
| `src/agents/planner/agent.py` | 465 lines removed from old LLM loop; now thin: `run_semantic_plan` → validate → `_plan_to_output`. `planner_node` synthesizes research on MICRO. |
| `src/integrations/cursor/semantic_plan_runner.py` | **New.** Builds prompt via `PromptBuilder`, calls `gateway.ask()`, parses `ExecutionPlan` JSON. |
| `src/core/plan_validation.py` | `validate_instruction_edits()` replaces `validate_edit_patches` + `validate_semantic_edits`. `sync_files_to_change_from_edit_steps()`. |
| `src/core/schemas.py` | `ExecutionStep` instruction fields; pydantic validator rejects legacy patches on EDIT. |
| `skills/planner/planner.md` | Cursor explores repo; instruction contract; caution_flag; no old_string. |
| `tests/test_planner.py` | Rewritten for semantic plan mocking; `test_synthesize_micro_research_output_extracts_paths`. |
| `tests/test_planner_grounding.py` | **Deleted** (grounding now Cursor's job). |

### 6.4 MICRO route planner behavior

| | |
|---|---|
| **Problem** | MICRO skips research agent — planner had no `research_output`. |
| **Changes** | `synthesize_micro_research_output(state)` builds minimal `ResearchOutput` from ticket title/problem + `extract_repo_file_paths_from_texts(..., repo_path)`. `planner_node` calls it when `route == MICRO` and research missing. |
| **Reason** | Ticket is self-locating; stub gives planner/Cursor enough to start. Real exploration still happens in `run_semantic_plan`. Bug fix Jul 9: must pass `repo_path` into file extraction. |

---

## 7. Executor Validation Pattern — Deep Dive

### 7.1 Validation stack (post-edit, per EDIT step)

```
1. capture_pre_edit (approximate_line or READ span)
2. Cursor batch edit (pass 1 or 2)
3. noop check (pre_hash == post_hash → LLM validates intent already satisfied)
4. structural_check:
   - Python: ast.parse → hard fail
   - TS/JS: brace balance → warning only, passed to LLM
5. build_validation_context (validation_context.py):
   - Level 1: git-diff hunks → expand to semantic parent (method/class/function)
   - Level 2: full-file fallback
6. LLM semantic validation (validation.py):
   - Loads executor skill as policy
   - Scope: planner instruction only
   - PASS: equivalent implementations, no-op OK if intent satisfied
   - FAIL: security property missing in edited region
   - observations don't affect pass boolean
7. If any fail + pass 2 available → batch repair with retry_notes
8. VERIFY steps: read + verify_intent_marker (literal substring, unchanged)
```

### 7.2 Key executor files changed

| File | Change |
|------|--------|
| `src/agents/executor/agent.py` | Major rewrite (~1120 lines touched). Batch-only edit path. `_run_edit_batch_phase`, `_validate_all_edits`, `_evaluate_post_edit`. Removed per-file edit loop. |
| `src/agents/executor/validation.py` | `ValidationResult`, `SemanticValidationResponse`, `validate_edit`, `validate_edit_progressive`, `build_retry_note`, `log_edit_outcome`. |
| `src/agents/executor/validation_context.py` | **New.** `diff_changed_line_ranges`, `_python_semantic_scope`, `_expand_to_semantic_parent`, `warrants_context_expansion`, `ContextLevel` enum. |
| `src/agents/executor/structural_check.py` | **New.** `check_file_structure`, `is_python_hard_fail`. |
| `skills/executor/executor.md` | Full semantic validation policy, non-errors list, caution_flag handling, anti-patterns. |
| `skills/executor/references/edit_patterns.md` | Edit pattern reference (existing, updated). |
| `tests/test_executor_edit_validation.py` | Rewritten for batch + LLM validation flow. |
| `tests/test_executor_batch_flow.py` | **New.** Asserts 2 batch passes, repair call count. |

### 7.3 Config / settings

| Setting | Default | Purpose |
|---------|---------|---------|
| `CURSOR_MAX_EDIT_ATTEMPTS` | 2 | Max whole-plan Cursor batch passes |
| `SEMANTIC_SOFT_VALIDATION_ENABLED` | true | Legacy flag from soft-validation v3 (harness only post-`c60a004`) |
| `semantic_warning_density_threshold` | 0.5 | Legacy density valve threshold |

---

## 8. Orchestrator — All Changes

### 8.1 Skill (`skills/orchestrator/orchestrator.md`)

| Change | Reason |
|--------|--------|
| Step 2: Feature/Enhancement EXIT for non-vuln tickets without repo | Pipeline scoped to defect/vuln remediation |
| Step 3: `risk_floor` decoupled from route | Security label ≠ must run research |
| Step 3.5: User prompt route override (highest priority except EXIT) | You requested explicit MICRO/FULL/EXIT in user_prompt |
| Step 4: Locatability check + 4b FULL hard stops | File named → MICRO eligible; null repo_path / retries → FULL |
| Removed `SKIP_RESEARCH` as output route | Merged into MICRO |
| Step 5b: `vulnerability_category` + `recommended_security_skill` | Orchestrator owns classification for research skill loading |
| Anti-pattern: never let security label alone force FULL | Prevents over-routing |

### 8.2 Python (`src/agents/orchestrator/agent.py`)

| Change | Reason |
|--------|--------|
| `run_orchestrator_routing(..., repo_path=, repo_profile=)` | Pass resolved checkout into context |
| `apply_user_route_override(decision, user_prompt)` | Enforce user route after LLM |
| `sanitize_routing_decision` + `normalize_routing_route` | Coerce SKIP_RESEARCH → MICRO |
| `apply_orchestrator_ticket_analysis` | Write vuln category + skill stem to ticket |
| Imports `skill_manager.PromptBuilder` (was `prompt_builder` / `skill_registry`) | Unified skill loading |

### 8.3 Graph / pipeline

| File | Change |
|------|--------|
| `src/core/graph.py` | `route_after_orchestrator`: MICRO → `planning_hag` (not `micro_confirm`) |
| `src/core/schemas.py` | `RoutingDecision` fields: `vulnerability_category`, `recommended_security_skill` |
| `src/core/orchestrator_context.py` | `build_orchestrator_routing_context`, `parse_explicit_user_route`, `apply_user_route_override` |
| `src/pipeline/runner.py` | MICRO research synthesis; `resolve_routing` accepts repo_path |

---

## 9. Complete Change Inventory (by commit)

### Commit `2d0208f` — Jul 4 — "Tested orchestrator, research agent only..."

**Planner**
- Rewrote `agent.py`: Cursor semantic plan runner replaces in-process LLM planner
- Added `semantic_plan_runner.py`
- Added `plan_validation.py` instruction path (initial)
- Added `semantic_target.py` (841 lines) — later removed
- Updated `planner.md`, `plan_schema.md`
- `tests/test_planner.py` major rewrite; deleted `test_planner_grounding.py`

**Executor**
- Initial `validation.py`, instruction-based edit brief
- `executor.md` skill updates for semantic validation
- `tests/test_executor.py`, `test_executor_edit_validation.py` rewrites

**Orchestrator**
- `orchestrator.md`: locatability, risk_floor, security classification
- `agent.py`: skill_manager migration

**Evals (new)**
- Full harness: artifacts, judges, preflight, `run_judge_eval.py`
- Golden unit tests: dvpwa, vampi, webgoat, juiceshop, wrongsecrets
- `eval_commands.md`, baselines, scoring

**Infrastructure**
- `skill_manager.py` replaces `skill_registry.py` + `skills_loader.py`
- 12 `docs/Testing_repos_info/*.md` repo guides
- `Makefile` eval targets
- Removed `.env.local` from tracking

### Commit `c60a004` — Jul 8 — "2nd Time Tested, Improved validation..."

**Critical pivot**
- **Deleted** `src/core/semantic_target.py` (−841 lines)
- `plan_validation.py` simplified to `validate_instruction_edits` (no symbol resolution)
- Added `validation_context.py` (+452), expanded `validation.py` (+379)
- Added `structural_check.py` (+89)
- Executor `agent.py` batch redesign finalized (+691/− lines)

**Planner**
- `planner.md`: instruction contract, caution_flag, VERIFY intent_marker rules
- `semantic_plan_runner.py`: explicit no old_string in prompt
- `agent.py`: `synthesize_micro_research_output` wiring

**Evals**
- `failure-modes.md` (+366): full Pass 1 vs Pass 2 analysis
- `evaluator_architecture.md` (+979)
- `executor_judge.py`, `planner_judge.py`, `llm_judge_base.py`
- `artifact_capture.py` (+281)
- `baselines/current.json` promoted to Pass 2 (72.2%) → later Pass 3 (100%, `run_030`)
- `targets.py` harness extensions

**Docs**
- `AGENT_PIPELINE_GUIDE.md` (+706)

**API cleanup**
- Removed `src/api/jira_filter.py`, trimmed `auth.py`

### Commit `fb45b0f` — Jul 8 — "Added communication endpoints"

- `src/api/execution_events.py` (+147): SSE/streaming pipeline events
- `src/api/pause_display.py` (+71): human gate pause UI
- `src/api/redis_store.py` (+57): job state
- `src/api/main.py`, `ticket_jobs.py` expanded
- `evals/DOCS/LangSmith_2ndTest_Reasoning.txt` (+342): per-case judge reasoning
- `evals/golden/eval_description_depthness.md` (+202)
- `tests/test_api.py`, `test_execution_events.py`

### Uncommitted / Jul 9 (post-commit, from chats)

- `evals/harness/targets.py`: `_routing_repo_context` for MICRO evals
- `src/pipeline/runner.py`: forward `repo_path` to `resolve_routing`
- `tests/test_evals_harness.py`: routing repo context test
- `src/agents/planner/agent.py`: `repo_path` fix in `synthesize_micro_research_output`
- `tests/test_planner.py`: `test_synthesize_micro_research_output_extracts_paths`
- `evals/evaluators/scoring.py`: SKIP_RESEARCH ≡ MICRO normalization

---

## 10. Problem → Changes → Reason (Quick Reference Index)

| # | Problem | Changes | Reason |
|---|---------|---------|--------|
| 1 | Patch-based plans brittle | Instruction + Cursor semantic plan | Survives formatting; Cursor grounds on disk |
| 2 | 40% NO_OUTPUT from symbol resolver | Delete `semantic_target.py`; `validate_instruction_edits` only | False negatives blocked good plans |
| 3 | Executor exact-match validation | LLM semantic validation + progressive context | Judge security property, not patch shape |
| 4 | Inconsistent batching/retries | Always 2 batch passes; `_run_edit_batch_phase` | Predictable Cursor budget; real repair |
| 5 | Incomplete Cursor edits | Inline Acceptance checklist in planner instruction | Retry needs concrete targets |
| 6 | Ambiguous symbols hard-fail | `caution_flag` (planner) + executor policy | Disambiguate without aborting |
| 7 | Security label → always FULL | `risk_floor` decoupled; locatability check | HIGH risk tickets can be MICRO |
| 8 | SKIP_RESEARCH / MICRO duplicate | Merge to MICRO; Gate 2 on MICRO | Simpler routes; plan still reviewed |
| 9 | Eval can't score semantic output | LLM judges + artifact capture + preflight | Hybrid eval matches new architecture |
| 10 | Pass 1 baseline 16.7% | Above changes combined | Pass 2: 72.2% (+55.5 pp) |
| 11 | MICRO evals route FULL | Pass `repo_path` in eval harness | Context bug, not LLM bug |
| 12 | user_prompt ignored for routing | Step 3.5 + `apply_user_route_override` | Explicit user intent honored |
| 13 | MICRO planner crash | `repo_path` in `synthesize_micro_research_output` | File extraction requires repo root |
| 14 | TS/JS structural false positives | Brace check → warning; LLM decides | Template literals/JSX break counters |
| 15 | G-18 regression (YAML corrupt) | Skills improvements (Pass 3) | **Resolved:** plain Secret removed |
| 16 | G-14 partial (innerHTML remains) | Multi-file component+template planning | **Resolved:** template sink removed |
| 17 | G-16 partial (async verify) | Sync API preservation in executor skill | **Resolved:** sync `jwt.verify` RS256 |
| 18 | Pass 2 plateau at 72.2% | Planner/executor/research skill updates | Pass 3: **100%** (+27.8 pp) |

---

## 11. Quality Caveats (Pass 3) — Non-blocking

> All 18 cases pass. Items below are quality improvements, not failures.

| Case | Executor score | Caveat | Suggested follow-up |
|------|----------------|--------|---------------------|
| G-18 | 70 | `sealed-funnystuff.json` manifests not in diff; scripts depend on them | Ensure SealedSecret manifests are created and committed |
| G-05 | 98 (planner 70) | Planner plan quality variance | Review planner skill for BAC auth-gating patterns |

**Pass 3 result:** 18/18 = **100%** (exceeds prior projection of 94.4%).

---

## 12. Chat Transcript Index

| Topic | Transcript ID (link) |
|-------|----------------------|
| Eval framework redesign | [Eval redesign Jul 4](2c8b61db-6840-459c-bc9c-3ff44a9c94ca) |
| Soft validation pipeline v1–v3 | [Soft validation Jul 6](27b3e693-d795-41d6-b787-71444872f6d6) |
| Executor two-phase batch | [Executor batch Jul 6](678c30fe-637f-45b8-a80b-5d9740655616) |
| Agent skill / Cursor architecture Q&A | [Agent architecture Jul 8](c63a7306-dbd5-4e28-b51b-c05b61b1a3ea) |
| MICRO routing + SKIP_RESEARCH merge | [MICRO merge Jul 8](f444758c-29ca-4d19-ab4e-a3b7767fef42) |
| MICRO harness repo_path fix | [MICRO routing fix Jul 9](80b39956-d8ee-4a32-8780-22a78ed6c278) |

---

## 13. Key Artifacts to Review

| Artifact | Path |
|----------|------|
| Pass 3 baseline | `evals/baselines/current.json` |
| Failure taxonomy | `evals/DOCS/failure-modes.md` |
| Eval results quick ref | `evals/DOCS/checking_evals_result.md` |
| Per-case judge reasoning | `evals/DOCS/LangSmith_2ndTest_Reasoning.txt` |
| Pipeline guide | `docs/AGENT_PIPELINE_GUIDE.md` |
| Eval commands | `evals/DOCS/eval_commands.md` |
| Run artifacts (Pass 3) | `evals/runs/run_030/` |
| Run artifacts (Pass 2) | `evals/runs/mixed_15_afterSemanticANDValidationChanges/` |
| Run artifacts (Pass 2) | `evals/runs/mixed_3_afterSemanticANDVlidationChanges/` |

---

*Generated: Jul 12, 2026 · Covers git commits `2d0208f`–`fb45b0f`, eval Pass 3 (`run_030`), and agent transcripts Jul 4–11.*
