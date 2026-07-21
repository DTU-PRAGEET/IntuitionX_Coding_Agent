# Daily Diary — Jul 10–13, 2026

> **Period covered:** Friday Jul 10 → Monday Jul 13, 2026  
> **Sources:** git commits `5ec0c2f` → `d0b31d9` → `0e0aa80` → `869ae91` → `b57660e`, agent chats Jul 10–13, eval run `run_030`

---



**5. Updated all eval comparison documents for run_030**
	a. Problem = Docs still showed Pass 2 (72.2%) as current baseline after Pass 3 completed.
	b. Changes =
		- `evals/baselines/current.json` — promoted to Pass 3 (100%)
		- `evals/DOCS/failure-modes.md` — added Pass 3 section, cross-pass matrix, recovered-case analysis
		- `evals/DOCS/checking_evals_result.md` — rewritten as Pass 3 quick reference
		- `evals/README.md`, `evals/DOCS/eval_commands.md` — baseline tables updated
		- `evals/runs/run_030/report.json` — generated scorecard
		- `daily_dairy.md` (prior version) — Pass 3 metrics added
	c. Reason = Single source of truth for "what's our current score" — avoids confusion when comparing runs or writing reports.

**6. Preflight vs judge score divergence explained**
	a. Problem = Judge scores went up (Pass 2 → Pass 3) but preflight scores appeared to go down — unclear if pipeline was regressing.
	b. Changes = Investigation only (no code). Clarified: **preflight** = deterministic checks (schema, file exists, triad shape, fatal JSON errors). **Judge** = LLM semantic quality score. As plans got richer (more steps, more VERIFY markers), preflight warns more (e.g. extra steps, soft flags) while judge rewards better fixes.
	c. Reason = These measure different things — preflight is a gate, judge is quality. Rising judge + "lower" preflight is expected when plans get more detailed.

---

## Executor / Validation Understanding

**7. Executor validation flow documented**
	a. Problem = Unclear where LLM validation runs vs Python checks, and what `_run_edit_batch_phase` / `_validate_all_edits` / `_evaluate_post_edit` do.
	b. Changes = Investigation and explanation (no major code change from this chat). Mapped flow:
		```
		run_executor()
		  └── _run_edit_batch_phase()        ← after Cursor batch edits
		        └── _validate_all_edits()    ← each EDIT step
		              └── _evaluate_post_edit()
		                    ├── validate_edit()         ← LLM (noop case)
		                    └── validate_edit_progressive() ← LLM + diff context
		```
		Also documented: `structural_check.py` (ast.parse, brace warnings), preflight in `validation.py`, post-batch VERIFY pattern checks.
	c. Reason = You needed to know validation cost (Cursor calls vs LLM calls) before changing the batch design.

**8. READ/EDIT/VERIFY triad ownership clarified**
	a. Problem = Confusion whether Python executor should replay READ steps during validation, or whether READ is only for Cursor's batch edit context. Validation was failing on READ steps for files that were only in the plan for Cursor context.
	b. Changes = Skills update (item 2 above) made this explicit: READ steps are **delegated to Cursor batch**; Python does preflight + LLM validation + post-batch VERIFY only. Reverted an earlier agent edit that tried to replay READs in Python validation (you rejected that approach).
	c. Reason = Your original Jul 4 design intent: one Cursor batch call for all edits; validation checks files directly, not via plan READ steps.

**9. Executor validation failure on SPACE-129070 (WrongSecrets)**
	a. Problem = Pipeline failed at executor READ step on `WrongSecretsConstants.java` — looked like validation bug after skills changes.
	b. Changes = Investigation traced to plan containing READ for a file path that didn't match repo layout / prior eval mutations. Root cause: repo state dirty from eval runs + plan READ steps being executed by old executor path before skills fix.
	c. Reason = Not a skills regression — combination of dirty checkout and READ-step execution policy (fixed by item 8).

**10. PR not raised on failed validation run**
	a. Problem = A run stopped at GATE_2 / executor without raising PR; no `executor_validation_failure` in logs.
	b. Changes = Traced job state: failure was on executor READ step (step 17), not validation LLM. PR generation only runs after edit batch completes. Item 1 (PR on validation LLM error) addresses a different failure mode.
	c. Reason = Distinguish "failed before edits" vs "failed after edits" — PR policy differs.

---

## Planner / Pipeline Debugging

**11. Missing EDIT steps in planner output (SPACE-129068)**
	a. Problem = Same ticket produced ~30-step plans with EDIT steps yesterday, but later runs showed mostly READ/VERIFY and zero or one EDIT — looked like a regression on `Skills_Hardening_Experiment` branch.
	b. Changes = Full pipeline trace. Findings:
		- **Primary cause:** eval runs modified repos locally (`.cache/eval_repos/`, worktrees) — planner/Cursor saw already-fixed code and produced verify-only plans.
		- **Secondary:** Redis `failed:*` entries and retry context influenced planner on repeated ticket runs.
		- **Not caused by:** skills changes alone (main branch with clean repo still produced detailed plans).
		c. Recommended: reset eval repo checkouts / use fresh worktrees before demo runs.
	c. Reason = "No EDIT steps" usually means the agent thinks the fix is already applied — dirty repo, not broken planner.

**12. OpenVPN / Sprinklr router 403 when orchestrator routes**
	a. Problem = Orchestrator routing failed with `403 Forbidden` even with VPN on — looked like network issue.
	b. Changes = Investigation only. Probed `qa6-intuitionx-llm-router-v2.sprinklr.com` — real error: **"Generative AI Feature is disabled"** for `client_identifier=backend-platform-dev`, not VPN failure. Documented in chat; `sprinklr_router.py` error message is misleading (says check VPN for all 403s).
	c. Reason = VPN gets you to the router; entitlement (`client_identifier` + `partner_id`) gates the model. Fix = ask platform team to enable Generative AI or use approved identifier.

**13. LLM Router SDK vs current HTTP integration**
	a. Problem = Asked if switching to official `llm_router` SDK would fix 403 and whether `client_identifier` is still needed.
	b. Changes = Investigation only. Confirmed: SDK uses same router API; `client_identifier` + `partner_id` still required; SDK won't bypass feature-disable 403. Current `sprinklr_router.py` already matches SDK contract.
	c. Reason = Avoid unnecessary refactor — the blocker is entitlement, not HTTP client choice.

**14. Orchestrator routing context — how Jira data reaches the LLM**
	a. Problem = Unclear where `orchestrator.md` Step 2–3 happen in code — are we sending raw Jira ticket to the orchestrator LLM?
	b. Changes = Investigation only. Traced: `fetch_jira_ticket()` → `build_orchestrator_routing_context()` → compact JSON (title, labels, status, description preview, repo links) → `PromptBuilder` → LLM. Not raw ticket blob.
	c. Reason = Understand routing inputs before changing security-label → route logic.

**15. Orchestrator Step 3 security keywords**
	a. Problem = Step 3 lists keywords like `auth`, `security`, `cve` — seemed wrong if we only handle security tickets anyway.
	b. Changes = Investigation only. Clarified: keywords set `risk_floor=HIGH`, they do **not** alone force FULL route (see item 25 from Jul 8 arc). Step 4 locatability gates MICRO vs FULL.
	c. Reason = Security label = high risk, not "must run full research."

---

## Codebase Cleanup (M1.3)

**16. Codebase maintainability audit (audit-only)**
	a. Problem = Codebase grew sprawling — ~150 Python files, duplicate orchestration stacks, many zero-call-site functions. Hard to know what's safe to remove.
	b. Changes = Created `docs/cleanup-audit.md` (audit only, no code changes). Catalogued:
		- ~89 public zero-reference symbols in `src/`
		- **Largest dead cluster:** M1.3 `PipelineOrchestrator` + sub-agents (~1,500+ lines, zero importers)
		- Merge candidates: `preflight.py` + `preflight_scores.py`, `edit_step.py` inline, duplicate Jira clients
		- Out-of-scope: LangGraph entry points, skills, eval harness, API routes
	c. Reason = Need a evidence-based cleanup plan before deleting anything — you required proof of unreachable code.

**17. Commented out dead M1.3 orchestration stack**
	a. Problem = `PipelineOrchestrator`, `CursorResearchSubAgent`, `CursorPlanSubAgent`, `CursorImplementSubAgent`, and cursor runners (`plan_runner.py`, `research_runner.py`, `implement_runner.py`) had **zero importers** — parallel unused stack next to LangGraph pipeline.
	b. Changes = Commented out (not deleted) per audit, with `# --- M1.3 commented out ---` markers in:
		- `src/core/orchestrator_loop.py` (`PipelineOrchestrator`)
		- `src/core/sub_agent.py`, `src/core/agent_context.py`
		- `src/agents/research/sub_agent.py`, `src/agents/planner/sub_agent.py`, `src/agents/executor/sub_agent.py`
		- `src/integrations/cursor/plan_runner.py`, `research_runner.py`, `implement_runner.py`
		- Removed stale tests: `test_orchestrator_loop.py`, `test_m13_sub_agents.py`
		- Deleted obsolete docs: `docs/Improvements.txt`, `evals/DOCS/LangSmith_2ndTest_Reasoning.txt` (content moved to other docs)
	c. Reason = LangGraph + `src/pipeline/runner.py` is the live path. Keeping commented code allows quick rollback if something breaks.

**18. Commented out unused functions from cleanup-audit (lines 111–130)**
	a. Problem = Audit listed 14 dead symbols still cluttering modules — zero call sites, confusing for maintainers.
	b. Changes = Commented out (kept `raise_gitlab_mr` as requested):
		- `format_micro_confirm` — `formatters.py`
		- `fetch_ticket_by_key`, `fetch_tickets_from_jql` — `jira_client.py`
		- `raw_to_legacy_fields` — `jira_schema.py`
		- `rank_candidate_files` + helpers — `file_discovery.py`
		- `validate_repository` — `git_worktree.py`
		- `validate_execution_plan_strict` — `plan_validation.py`
		- `log_edit_patch`, `old_string_in_file`, `file_git_diff` — `repo_snapshot.py`
		- `search_structured` — `gateway.py`
		- `FailedAttemptRecord` — `schemas.py`
		- `SkillManager` class — `skill_manager.py`
		- `scripts/add_docstrings.py` — entire file commented
		- `evals/evaluators/legacy_scoring.py` — commented; imports redirected to `scoring.py`
		- Fixed dead import in `tests/test_core.py`
	c. Reason = Reduce noise without deleting — main pipeline path unchanged; 366 tests still pass after comment-out.

**19. Merged functions and deleted 7 files (cleanup commit)**
	a. Problem = After comment-out pass, duplicate thin modules remained — `preflight_scores.py` duplicated `preflight.py`, `edit_step.py` was one function, `langchain_tools.py` / `run_context.py` / `planner/tools/builder.py` unused, `graph_outcome.py` orphaned, `api/auth.py` empty stub.
	b. Changes = **Deleted 7 files:**
		- `evals/evaluators/preflight_scores.py` → merged into `preflight.py`
		- `src/core/edit_step.py` → `is_instruction_edit` inlined into `plan_validation.py`
		- `src/agents/planner/tools/builder.py`
		- `src/agents/research/tools/langchain_tools.py`
		- `src/agents/research/run_context.py`
		- `src/pipeline/graph_outcome.py` → logic moved to `graph_runner.py`
		- `src/api/auth.py`
		Also: `repo_profile.py` helpers consolidated; `jira.py` cleanup; `middleware.py` tweaks; `api/main.py` auth import removed.
	c. Reason = Fewer files = easier navigation; merge related code that's always used together.

**20. `format_micro_confirm` analysis**
	a. Problem = Considering removing `format_micro_confirm` — needed to know what it does and if safe.
	b. Changes = Investigation: builds HITL "quick confirm" message for old MICRO-only gate. **Zero call sites** — graph uses `format_gate2_request` for all routes including MICRO. Stale references remain in `state.py`, `chainlit_app.py`, `pipeline_event_factory.py`.
	c. Reason = Dead code from pre-Jul-8 graph when MICRO had its own confirm node. Safe to remove/comment (done in item 18).

**21. Jira linked/dependent tickets — how we handle them**
	a. Problem = Before commenting out `fetch_tickets_from_jql`, needed to know if pipeline fetches related tickets or only the input ticket.
	b. Changes = Investigation only. Confirmed:
		- Pipeline processes **one ticket ID** at a time.
		- `fetch_jira_ticket()` reads `issuelinks` from the single Jira API response — embedded `outwardIssue`/`inwardIssue` give key + summary + status only (no second fetch).
		- `JiraTicket.dependencies` is populated but **not passed** to orchestrator/planner/research prompts today.
	c. Reason = Commenting out batch JQL fetch is safe — related-ticket context was never fully wired. Future work would need explicit linked-ticket fetch + prompt injection.

---

## Prior Work Referenced (Jul 4–9 arc, still relevant)

**22. Instruction-based planner (Cursor semantic plan)**
	a. Problem = Patch-based plans (`old_string`/`new_string`) were brittle — whitespace drift, multi-file, TypeScript. Pass 1: 40% failures were `NO_OUTPUT` from planner hard-stops.
	b. Changes = Planner rewritten to `run_semantic_plan()` via Cursor Ask. `semantic_target.py` (841 lines) deleted; `validate_instruction_edits()` replaces symbol resolution.
	c. Reason = Cursor explores repo at plan time; instructions survive formatting drift.

**23. Executor LLM semantic validation + two-phase batch**
	a. Problem = Exact `old_string` match validation was arbitrary. Per-file retry loop wasted Cursor calls; batch repair often didn't re-call Cursor.
	b. Changes = `validation.py`, `validation_context.py`, `structural_check.py`. Always 2 batch passes: all EDITs → validate all → repair failed only. `CURSOR_MAX_EDIT_ATTEMPTS=2`.
	c. Reason = Validate security property in instruction scope, not patch shape. Predictable Cursor budget.

**24. Eval framework redesign (LLM-as-Judge)**
	a. Problem = Old evals couldn't score semantic plans or instruction-based executor output.
	b. Changes = `planner_judge.py`, `executor_judge.py`, `preflight.py`, artifact capture, `failure-modes.md`, baselines. Hybrid deterministic preflight + LLM judge.
	c. Reason = Match eval architecture to new pipeline; artifacts enable per-case debugging.

**25. Fine-tuned orchestrator routing**
	a. Problem = Security labeled tickets (auth, sqli, etc.) forced the FULL route unconditionally — even for self-locating, single-file fixes that didn't need a full Research pass.
	b. Changes = `orchestrator.md` Step 3: security labels now set `risk_floor=HIGH` only, and no longer force the route by themselves. Step 4: added a locatability check (is the file named in the ticket?) that gates MICRO vs FULL independently of risk level. `RoutingDecision` schema extended accordingly.
	c. Reason = HIGH risk doesn't imply "needs full research" — many CVE fixes are one named file with a bounded change; forcing FULL wasted Research-agent time and cost on cases that didn't need it.

**26. Merge SKIP_RESEARCH → MICRO + Gate 2 on MICRO**
	a. Problem = `SKIP_RESEARCH` and `MICRO` duplicated behavior; MICRO skipped human plan approval (Gate 2).
	b. Changes = Route type: `FULL | MICRO | EXIT` only. MICRO → `planning_hag` (Gate 2). `synthesize_micro_research_output()` when no research. User-prompt route override (Step 3.5).
	c. Reason = Simpler mental model; plan still human-reviewed before executor.

**27. MICRO routing eval harness fix (Jul 9)**
	a. Problem = Eval harness called `resolve_routing()` without `repo_path` — all 7 MICRO golden cases routed FULL despite correct LLM reasoning.
	b. Changes = `evals/harness/targets.py`: `_routing_repo_context()`. `runner.py` forwards `repo_path` to routing.
	c. Reason = Production resolves repo before routing; evals must mirror that.

---

## Production / Portability (Investigation)

**28. Portability & production-readiness audit (Jul 10)**
	a. Problem = Pipeline only tested locally — need honest assessment of what blocks production deployment.
	b. Changes = Investigation report (chat only). Key findings: Cursor CLI install is local-only blocker; Redis optional; Jira/GitHub credentials required; no K8s/Helm manifests yet; state in LangGraph checkpoints + Redis job store.
	c. Reason = Understand gap between "works on my machine" and deployable service.

**29. Cursor SDK install failure as portability blocker**
	a. Problem = `cursor` CLI must be pre-installed locally — fails in clean environments. Question: is there a substitute?
	b. Changes = Investigation only. Options: pre-bake Cursor in Docker image, remote Cursor gateway service, or fallback to dry-run/mock mode for CI. No code change yet.
	c. Reason = Without headless Cursor in the image, the project can't run outside your laptop.

---
