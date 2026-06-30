# Solved Errors Log

A consolidated catalog of every error encountered **and resolved** across our previous
working sessions on the Sprinklr Jira Solver coding agent. Entries were extracted from the
agent chat transcripts and grouped by theme. Each item lists the symptom, root cause, the
fix applied, and the source conversation id(s).

---

## 1. Environment, Dependencies & Python Compatibility

- **Python 3.9 type-hint incompatibility (PEP 604 unions)**
  - Symptom: Import/runtime failures from `X | Y` union syntax / modern annotations (e.g. `end_line: int | None`, `str | None`) on the local Python 3.9 runtime.
  - Root cause: Code used 3.10+ syntax without deferred annotation evaluation.
  - Fix: Added `from __future__ import annotations` across core modules; switched offending hints to `Optional[...]` (`file_tools.py`, `jira_schema.py`, `jira_client.py`, `memory.py`); added `eval_type_backport` to `requirements.txt`.
  - Source: `0ac60418`, `2b045552`

- **`pytest` / `structlog` not installed**
  - Symptom: `ModuleNotFoundError: No module named 'pytest'` (and `structlog`) on the first test run.
  - Root cause: Dev dependencies missing in the environment.
  - Fix: Installed `pytest structlog pydantic`; converted `tests/test_core.py` to `unittest` so core tests could run without pytest.
  - Source: `0ac60418`, `2b045552`

- **LangGraph SQLite checkpoint dependency missing**
  - Symptom: Graph/checkpoint import failed when building the LangGraph app.
  - Root cause: `langgraph-checkpoint-sqlite` was not installed.
  - Fix: Installed `langgraph-checkpoint-sqlite`; verified `build_graph()` imports.
  - Source: `0ac60418`

- **Makefile test target used the wrong Python**
  - Symptom: `make test` / `orchestrator-m14` failed when system `python3` lacked the venv packages.
  - Root cause: Hardcoded `python3`/`pytest`.
  - Fix: `PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)` in `Makefile`.
  - Source: `8d011abc`

- **`file_index.py` used nonexistent `Path.walk()`**
  - Symptom: Runtime failure building the repository file index on Python versions without `Path.walk()`.
  - Root cause: Used the 3.12+ `root.walk()` API.
  - Fix: Replaced with `os.walk(root)` in `src/core/skill_scripts/file_index.py`.
  - Source: `ca6d53d0`

---

## 2. Imports, Modules & Package Wiring

- **Missing `src.core.tracing` module**
  - Symptom: `ModuleNotFoundError: No module named 'src.core.tracing'`.
  - Root cause: Orchestrator/agents imported a module that was never created.
  - Fix: Added `src/core/tracing.py` with `apply_trace_metadata()` and `PROMPT_VERSIONS`.
  - Source: `1729523a`

- **Missing `agent_debug` (pipeline CLI blocker)**
  - Symptom: `NameError` / missing `agent_debug` blocked `run_pipeline` CLI.
  - Root cause: Pipeline referenced `agent_debug` from a nonexistent `src/core/debug_log.py`.
  - Fix: Added a no-op stub `src/core/debug_log.py` with `agent_debug()`.
  - Source: `1729523a`

- **Chainlit `src` import failure**
  - Symptom: `ModuleNotFoundError: No module named 'src'` when running `chainlit run src/ui/chainlit_app.py`.
  - Root cause: Chainlit puts `src/ui/` on `sys.path`, not the repo root, so `from src.*` imports fail.
  - Fix: Insert the project root into `sys.path` at the top of `src/ui/chainlit_app.py`.
  - Source: `1729523a`

- **Broken `src/memory/__init__.py` exports**
  - Symptom: Import errors when importing from `src.memory`.
  - Root cause: `__init__.py` did not re-export cache helpers.
  - Fix: Re-export `get_failed_attempts` / `save_failed_attempt` from `cache.py`.
  - Source: `0ac60418`

- **Wrong `FixPlan` import path in the Cursor package**
  - Symptom: Import error loading `src.integrations.cursor`.
  - Root cause: `FixPlan` imported from `plan_runner` instead of the canonical schema module.
  - Fix: Import `FixPlan` from `src/core/schemas.py` in `src/integrations/cursor/__init__.py`.
  - Source: `97a9c0ca`

- **Circular import during ticket-repo tests**
  - Symptom: Test collection/import failure via `integrations/jira/__init__.py`.
  - Root cause: Link-parsing under `integrations/jira/` created an import cycle with `ticket_repo.py`.
  - Fix: Moved link parsing to `src/core/github_links.py`; slimmed `integrations/jira/__init__.py`.
  - Source: `5774b30a`

- **`test_context_builder` collection ImportError**
  - Symptom: `ImportError: cannot import name 'ContextBuilder' from 'src.core.context_builder'`.
  - Root cause: `context_builder.py` / `jira_compact.py` were commented out but still imported by the test.
  - Fix: Removed the dead trio (`context_builder.py`, `jira_compact.py`, `tests/test_context_builder.py`).
  - Source: `33697562`

---

## 3. Schemas, Pydantic & Plan Validation

- **`ExecutionStep.validate` shadowed Pydantic `BaseModel.validate`**
  - Symptom: Schema/test failures when adding a canonical `validate` field.
  - Root cause: Field name collided with the inherited Pydantic method.
  - Fix: Renamed to `step_validate` with JSON alias `"validate"`; added `model_config = {"populate_by_name": True}` in `src/core/schemas.py`; updated tests.
  - Source: `dde5f4df`

- **EDIT steps rejected when creating new files (no `old_string`)**
  - Symptom: `ValidationError: EDIT requires old_string and new_string` for create-file plans.
  - Root cause: Validator required both patch fields even when the executor uses `write` for new files.
  - Fix: Relaxed validator to require only `new_string`; updated tests.
  - Source: `dde5f4df`

- **Plan action enum migration broke tests/runtime**
  - Symptom: Failures after changing step actions from `LOCATE`/`SEARCH` to `READ`/`VERIFY`/`RUN_TESTS`.
  - Root cause: Schema, executor dispatch, mock plans, and tests still used legacy names.
  - Fix: Updated `schemas.py`, `state.py`, `executor/agent.py`, `pipeline/runner.py` and tests with a `_LEGACY_ACTION_MAP`.
  - Source: `ca6d53d0`

- **Planner test failure after schema rename (`invalid where` → `invalid file`)**
  - Symptom: `pytest.raises(ValueError, match="invalid where")` failed after canonical-field changes.
  - Root cause: Validator error message changed when `where`/`what`/`why` were removed.
  - Fix: Updated the assertion in `tests/test_planner.py`.
  - Source: `33697562`

- **Planner Pydantic validation — missing required fields**
  - Symptom: `plan_validation_failed`: `root_hypothesis`, `risk_level`, `execution_steps.N.step_id`, and/or `files_to_change` "Field required".
  - Root cause: The LLM omitted required schema headers; coercion only backfilled list defaults.
  - Fix: Auto-fill `root_hypothesis` from research, `risk_level` from `architectural_risk`, `step_id` as 1-based index, and default `files_to_change=[]`; added `_coerce_execution_plan_payload()` / `_parse_execution_plan()`; skip strict path validation for `RUN_TESTS` steps with `where: "."`; tests added.
  - Source: `5343a0fb`, `6958158d`

- **Missing `FixPlan` / `FixTarget` / `FixType` schema models**
  - Symptom: Import/validation failures in the M1.3 chain and `typed_fix_plan()`.
  - Root cause: Models referenced in `plan_runner.py`/tests were never added to the schema module.
  - Fix: Added `FixPlan`, `FixTarget`, `FixType` to `src/core/schemas.py`.
  - Source: `968c1aa4`

- **`typed_fix_plan` crash on empty `fix_plan`**
  - Symptom: `ValidationError` when an exception path left `fix_plan = {}`.
  - Root cause: No guard before Pydantic validation.
  - Fix: `typed_fix_plan()` now raises on empty dict; added `phase_failed` to `OrchestratorResult`; improved exception handling in `orchestrator_loop.py`.
  - Source: `968c1aa4`

---

## 4. Planner Logic & Grounding

- **Corrupted `planner/agent.py` after migration edits**
  - Symptom: Broken module with duplicated/misplaced functions (`load_research_report`, `_validate_plan_files`).
  - Root cause: A bad incremental `StrReplace` left invalid Python structure.
  - Fix: Restored proper function boundaries and imports.
  - Source: `ca6d53d0`

- **Degenerate plan with `where='.'`**
  - Symptom: `[read] exception ... Is a directory`; `[executor] step action=LOCATE file=.`.
  - Root cause: Research returned no `affected_files`; the planner emitted `where='.'`.
  - Fix: Added `_normalize_plan_paths()` / `_validate_plan_executable()`; injected the demo affected file in `pipeline/runner.py`; planner/pipeline tests.
  - Source: `dde5f4df`

- **Planner validated `old_string` against READ-tool output instead of the file**
  - Symptom: False positives/negatives in EDIT validation during verification.
  - Root cause: Compared against `result.output` from a READ call rather than file contents.
  - Fix: Read the file with `Path.read_text()` before comparing `old_string`.
  - Source: `ca6d53d0`

- **Planner skipped EDIT verification on already-read files (`seen_reads`)**
  - Symptom: EDIT `old_string` never validated against disk for paths already read in the same pass.
  - Root cause: Verification loop skipped EDIT checks when the path was in `seen_reads`.
  - Fix: Validate EDIT patches even when the path is in `seen_reads`.
  - Source: `62b333e6`

- **No-op EDIT validation failures (`old_string == new_string`)**
  - Symptom: `[planner] edit_patch_validation_failed` — identical old/new strings (e.g. XSS already mitigated by `autoescape` in `app.py`).
  - Root cause: Planner emitted no-op EDITs for cross-file mitigations.
  - Fix: Planner skill + `plan_schema.md` ban no-op EDITs; require READ→VERIFY-only for cross-file mitigations.
  - Source: `af4a6399`

- **Truncated / hallucinated planner patches**
  - Symptom: Partial primary edit; fallback no-op; patches ending mid-token (e.g. `'ret'`), `old_string == new_string`.
  - Root cause: The LLM truncated `old_string`/`new_string`; no hard rejection before execution.
  - Fix: Added `is_truncated_patch()` and `validate_edit_patches()` in `plan_validation.py`; planner grounding via `_ground_edit_patches_from_disk()`; `repo_snapshot.py` tracing.
  - Source: `87ac4960`, `62b333e6`

- **`old_string not found` due to trailing-newline drift**
  - Symptom: `edit_patch_validation_failed` / `old_string not found` with byte counts off by one (e.g. 1395 vs 1394, SHA mismatch) from an extra trailing `\n`.
  - Root cause: The planner copied content with one extra trailing newline; strict substring match failed.
  - Fix: Added `align_old_string_to_disk()` (full-file) and `align_old_string_in_content()` (snippet-level, bounded newline stripping) in `plan_validation.py`; wired into validation, grounding, and `_verify_plan_with_tools`; tests in `test_edit_patch_validation.py`.
  - Source: `62b333e6`, `c9560560`

- **Planner triad validation not wired**
  - Symptom: Incomplete/duplicated validation block after migration.
  - Root cause: A partial replace omitted `validate_execution_plan()`.
  - Fix: Integrated `validate_execution_plan()` / triad flags in `planner/agent.py`.
  - Source: `ca6d53d0`

---

## 5. Executor Logic, Edits & Verification

- **EDIT `old_string not found` (raw byte matching)**
  - Symptom: `Error: old_string not found in ...user_search_controller_vulnerable.py`.
  - Root cause: `EditTool` did a raw `content.count(old_string)` with no BOM/CRLF/fuzzy matching; worsened by stale worktrees.
  - Fix: Ported a pi-style matching engine (`src/tools/edit_diff.py`, `edit_args.py`); refactored `edit_tool.py` to read/write bytes (preserve CRLF); tests added.
  - Source: `55b40e47`

- **CRLF preservation test failure**
  - Symptom: CRLF test failed because `Path.read_text()` normalized `\r\n` → `\n`.
  - Root cause: Both the edit path and the test used `read_text()`.
  - Fix: `edit_tool.py` switched to `read_bytes().decode()` / `write_bytes()`; test asserts on `read_bytes()`.
  - Source: `55b40e47`

- **EDIT fallback JSON parsing failure**
  - Symptom: `Edit fallback failed: ...` when applying patches.
  - Root cause: Fallback used `parse_llm_json` with an ad-hoc patch type.
  - Fix: Regex-extract JSON and invoke `registry.get("edit").execute(...)` directly.
  - Source: `0ac60418`

- **Executor eagerly initialized the LLM on every run**
  - Symptom: Slow/failing paths because a LOW-tier LLM was created even when the deterministic EDIT succeeded.
  - Root cause: `get_llm(TaskComplexity.LOW)` called unconditionally.
  - Fix: Lazy-load `fallback_llm` only inside the EDIT fallback path.
  - Source: `dde5f4df`

- **Step failures missing actionable error context**
  - Symptom: Opaque, low-signal executor errors.
  - Root cause: Exceptions weren't re-raised with step/action/file context.
  - Fix: Wrapped step dispatch to raise `RuntimeError(f"Executor step {step_id} action=... file=... failed: {e}")`.
  - Source: `dde5f4df`

- **Executor blocked READ/VERIFY on out-of-scope files**
  - Symptom: `RuntimeError: Step 10 file 'sqli/dao/user.py' not in files_to_change and create_files=false`.
  - Root cause: `_assert_file_allowed` rejected verify-only READ steps outside the edit set.
  - Fix: Exempt READ/VERIFY steps from the strict allowlist; regression test added.
  - Source: `6fbcbb07`

- **Accept-by-intent: edit failed though the file was already fixed**
  - Symptom: `stage_failed=executor`, `Edit incomplete after fallback` / `No changes made`, while the target already had parameterized SQL.
  - Root cause: Planner emitted truncated/hallucinated patches; the executor rejected steps when plan text didn't match disk, ignoring security intent.
  - Fix: Added `_try_accept_by_intent()` in the executor plus planner disk-grounding.
  - Source: `87ac4960`

- **VERIFY grepped human prose instead of code**
  - Symptom: VERIFY failed with `grep -F 'Confirm the SQL injection vulnerability is removed'`.
  - Root cause: Used the planner's human-readable `what` text as the grep pattern.
  - Fix: Resolve patterns from `validate`, prior EDIT `new_string` lines, and code-like `what`; strip backticks from patterns.
  - Source: `dde5f4df`, `87ac4960`

- **VERIFY failed on an unchanged-but-retained function signature**
  - Symptom: `vulnerable pattern 'def search_users(query: str)...' still present` after a successful EDIT.
  - Root cause: All `old_string` lines were treated as forbidden, including lines kept in `new_string`.
  - Fix: Only forbid lines present in `old_string` but **not** in `new_string`.
  - Source: `87ac4960`

- **False `[bash] failed exit_code=1` during VERIFY**
  - Symptom: `[bash] failed exit_code=1` on forbidden-pattern greps even when the pipeline succeeded.
  - Root cause: `grep` exit code 1 ("no match") is success for forbidden checks, but `BashTool` logged it as failure.
  - Fix: Replaced shell `grep`/`test`/`head` with in-process checks (`_file_contains_fixed`, `_read_file_snippet`, `_resolve_repo_file`).
  - Source: `87ac4960`

- **Incomplete SQL fix wrongly reported as success**
  - Symptom: Executor changed the SQL string + added `params` but left `cursor.execute(sql)`; pipeline still passed.
  - Root cause: VERIFY stopped at the first matching grep; no post-EDIT completeness check.
  - Fix: Added `_validate_edit_completeness()`, multi-pattern VERIFY, forbidden-pattern checks, and `plan_deviations` tracking.
  - Source: `a1f55238`, `6fbcbb07`

- **False "incomplete" on full-file patches → unnecessary LLM fallback**
  - Symptom: `Executor step ... failed: LLM fallback rejected: identical old_string and new_string`, even though the Cursor edit succeeded (`post_hash == new_sha256`).
  - Root cause: `_validate_edit_completeness` used fragile per-line matching on whole-file patches and triggered fallback on an already-correct file.
  - Fix: Added SHA-match / SHA-delta short-circuit guards in `_execute_edit` and `_accept_batched_edit`; changed checks to `ln in content`; regression tests in `test_executor_edit_validation.py`.
  - Source: `196e9012`, `af4a6399`

- **Batched-edit acceptance for whole-file replacements**
  - Symptom: Valid batched edits rejected after the batching refactor.
  - Root cause: The batched path lacked the per-step SHA short-circuit.
  - Fix: SHA-match short-circuit in `_accept_batched_edit()`; consolidated per-file edits via `_apply_batched_edits()` in the Cursor gateway.
  - Source: `af4a6399`

- **Debug-log path resolved one directory too shallow**
  - Symptom: Instrumentation wrote to the wrong path.
  - Root cause: `parents[2]` instead of repo root.
  - Fix: `Path(__file__).resolve().parents[3] / ".cursor" / "debug-196e90.log"`.
  - Source: `196e9012`

---

## 6. Cursor / Edit Routing Migration

- **Executor tests broke after CursorGateway-only edit routing**
  - Symptom: Executor/pipeline tests failed once edits moved off `ToolRegistry.execute("edit"|"write")`.
  - Root cause: Tests/runtime still assumed local edit-tool behavior with no gateway fallback.
  - Fix: Added `LocalPatchCursorGateway`; routed `_execute_edit` through the gateway; restricted executor tools in `tool_permissions.yaml` and the tool builder; updated tests.
  - Source: `c12ee848`

- **CursorImplementSubAgent dry-run attempted worktree creation**
  - Symptom: M1.4 dry-run tests failed on git worktree setup.
  - Root cause: The dry-run path still created a worktree before returning the mock patch.
  - Fix: Early-return a mock `PatchResult` before worktree setup in `sub_agent.py`.
  - Source: `8d011abc`

- **Duplicate `run_cursor_plan` invocation**
  - Symptom: Planner sub-agent called `run_cursor_plan()` twice per run.
  - Root cause: A copy-paste bug left a second unconditional call.
  - Fix: Removed the duplicate; single gateway-injected invocation.
  - Source: `968c1aa4`

---

## 7. Git Worktrees & Pull Requests

- **Stale executor worktree reuse**
  - Symptom: Executor ran on a dirty reused worktree; the file differed from the planner's clean cache.
  - Root cause: `git_worktree.py` reused worktrees without resetting local modifications.
  - Fix: Rewrote `git_worktree.py` to reset/sync reused worktrees; added snapshot checks via `repo_snapshot.py`.
  - Source: `62b333e6`

- **GitHub PR creation 404**
  - Symptom: `GitHub API POST /repos/.../pulls failed (404): {"message":"Not Found"}`.
  - Root cause: Token lacked private-repo access (GitHub returns 404), wrong target repo vs ticket repo, missing base branch `develop` (only `main`), worktree reuse returned the wrong branch, push skipped with no commits.
  - Fix: Rewrote `mr_create.py` (`resolve_github_repo`, `github_repo_from_ticket`); added `_remote_branch_exists()` and branch resolution in `git_worktree.py`; tests in `test_mr_create.py`.
  - Source: `5774b30a`

---

## 8. Jira Integration

- **Invalid Jira API base URL**
  - Symptom: Jira fetch would fail once routing worked (UI page used as the API endpoint).
  - Root cause: `JIRA_SERVER_URL` was set to `/jira/for-you?tab=workedon`.
  - Fix: Corrected to `https://sprinklr-sandbox.atlassian.net` in `.env.local`.
  - Source: `0ac60418`

- **Jira partial-field `PropertyHolder` crash**
  - Symptom: `'PropertyHolder' object has no attribute 'assignee'` on ticket SCRUM-5.
  - Root cause: `JIRA_FETCH_FIELDS` omitted `assignee`/`reporter`; jira-python raises on missing fetched fields.
  - Fix: Safe field helpers in `jira.py`; added fields in `integrations/jira/client.py`; regression test in `test_jira_issue_fields.py`.
  - Source: `1729523a`

- **Jira TypedDict `.key` access**
  - Symptom: `'dict' object has no attribute 'key'` in Chainlit after a Jira fetch.
  - Root cause: `fetch_ticket_by_id()` used `ticket.key` on a `JiraTicket` TypedDict.
  - Fix: Use `ticket.get("issue_id") or ticket_id`; added `_issue_key()` and dict-safe issuelink parsing; test in `test_jira_client_parsed.py`.
  - Source: `1729523a`

- **Research ran against the wrong repo (garbled Jira repo links)**
  - Symptom: Research flagged the agent's own tests instead of `Testing-Security-demo`; `repo_links` split/truncated (e.g. `com/DTU-PRAGEET/...`).
  - Root cause: A broken `_extract_repo_links()` regex fragmented the description; Chainlit/orchestrator defaulted to the local repo path.
  - Fix: Added `github_links.py` + `ticket_repo.py`; fixed Jira parsing; orchestrator/Chainlit/executor now clone the ticket repo; tests in `test_jira_repo_links.py`, `test_ticket_repo.py`.
  - Source: `5774b30a`

---

## 9. LLM Providers & Routing

- **Orchestrator routing — Ollama connection refused**
  - Symptom: `Orchestrator routing validation failed: [Errno 61] Connection refused`.
  - Root cause: `LLM_PROVIDER=local` pointed at Ollama on `localhost:11434`, which wasn't running.
  - Fix: Added OpenRouter provider support in `llm.py` / `config.py`; switched `.env.local` to OpenRouter.
  - Source: `0ac60418`

- **OpenRouter model override applied to all providers**
  - Symptom: Wrong model selection when switching providers.
  - Root cause: `OPENROUTER_MODEL` was read unconditionally in `get_llm()`.
  - Fix: Apply the override only when `LLM_PROVIDER=openrouter`.
  - Source: `0ac60418`

- **Sprinklr LLM 403 Forbidden (Authorization header)**
  - Symptom: `POST .../chat/completions → 403 Forbidden`; orchestrator `routing_failed`.
  - Root cause: `ChatOpenAI(api_key="sprinklr")` sent `Authorization: Bearer sprinklr`; the Sprinklr contract uses a body `client_identifier` only.
  - Fix: Strip `authorization`/`api-key` headers in `SprinklrRouterTransport`; docs/tests.
  - Source: `98ee441a`

- **Sprinklr response shape not parsed by LangChain**
  - Symptom: Invoke failed after HTTP 200; `"object": "chat-completion"` vs OpenAI's `"chat.completion"`.
  - Root cause: Sprinklr's response object name differs.
  - Fix: `_normalize_openai_response()` rewrites the object type.
  - Source: `98ee441a`

- **Sprinklr probe/invoke empty response body**
  - Symptom: `PROBE FAILED` / parse errors on live responses.
  - Root cause: Response body not read before JSON parse on streaming httpx responses.
  - Fix: Call `response.read()` before error handling/normalization.
  - Source: `98ee441a`

- **Sprinklr 403 surfaced as a misleading "Connection error"**
  - Symptom: UI showed `Connection error` instead of a 403/VPN message.
  - Root cause: The transport raised `SprinklrRouterError`, which LangChain wrapped as `APIConnectionError`.
  - Fix: Return a JSON error response (→ `PermissionDeniedError` with actionable text); added proxy support via `HTTP_PROXY`/`HTTPS_PROXY`.
  - Source: `98ee441a`

- **Gemini list-block content parse failure**
  - Symptom: `Could not parse JSON from LLM response:` / `expected string or bytes-like object, got 'list'`.
  - Root cause: Gemini returns `content` as list blocks; `llm_content()` used `str(list)`.
  - Fix: Added `_flatten_content_blocks()` and `_extract_final_text()` in `runner_utils.py`; tests added.
  - Source: `55b40e47`

---

## 10. Research Agent

- **GraphRecursionError (mock-security loop)**
  - Symptom: `GraphRecursionError: limit reached`; pipeline never reached the planner.
  - Root cause: No `response_format=ResearchReport`; mock ticket pointed at a `.java` file; the agent looped on file search.
  - Fix: Added `response_format=ResearchReport`, mock demo hints, a recursion cap, and a `GraphRecursionError` fallback report.
  - Source: `87ac4960`

- **Research recursion limits too low**
  - Symptom: `Recursion limit of 6/20/56 reached without hitting a stop condition`.
  - Root cause: Limits (e.g. `max(iterations × 4, 12)`, manifest `recursion_limit: 6`) were too small for the expanded tool loops.
  - Fix: Raised to `max(max_research_iterations * 8, 40)`; dynamic floor `max(manifest.recursion_limit, tool_budget * 4 + 8)`; bumped the manifest to 40.
  - Source: `55b40e47`, `1b523fd1`

- **Research prompt hang before any LLM call**
  - Symptom: Pipeline hung in `rank_candidate_files` / `root.rglob("*")`.
  - Root cause: `rank_candidate_files` walked the whole repo per anchor (~16×), including `.venv`/`.git`.
  - Fix: Single pruned `os.walk`, 5000-file cap, skip generic anchors; skip pre-ranking for mock tickets.
  - Source: `87ac4960`

- **Primitive read-loop → recursion limit (never called `cursor_search`)**
  - Symptom: `GraphRecursionError`; the agent looped 20+ `read`/`ls`/`bash` calls, re-reading the same files.
  - Root cause: Only `cursor_search` was budgeted; primitives had no cap to force synthesis.
  - Fix: Rewrote `research.md` for a cursor-first flow; added `PRIMITIVE_CALL_BUDGET = 8`; lowered `_CURSOR_SEARCH_BUDGET` 6 → 2.
  - Source: `52f9732d`

- **Pseudo-absolute path resolution (`/sqli/...`)**
  - Symptom: `Error: file not found: /sqli/routes.py` despite `ls` showing it exists.
  - Root cause: `resolve_to_cwd` treated `/sqli/routes.py` as a real absolute path instead of joining with `repo_path`.
  - Fix: Remap pseudo-absolute paths under `repo_path` in `path_utils.py`; clearer not-found hints; tests added.
  - Source: `1b523fd1`

- **Research skill promised tools the runtime didn't expose**
  - Symptom: The skill said to use `read`/`ls`/`bash`, but the runtime only had `cursor_search`/`web_search`/`repo_profile`.
  - Root cause: Mismatch across `tool_permissions.yaml`, the tool builder, and the harness prompt.
  - Fix: Added `read`/`ls`/`bash` to `tool_permissions.yaml`; repo-scoped primitives in the builder; updated the harness.
  - Source: `af4a6399`

- **False BLOCKER: repo path not accessible**
  - Symptom: Gate 1 reported the repo wasn't checked out though the path existed.
  - Root cause: Unnormalized repo path; sub-agent omitted `repo_profile`/`anchors`/`memory`; `cursor_search` refused SQLi queries and the LLM hallucinated a blocker; sanitizer missed "not accessible" variants.
  - Fix: Normalize `repo_path` via `Path(...).resolve()`; pass full context; fail-fast if the repo is missing; gateway refusal detection + audit prompt; `bootstrap_repo_context()`; `_sanitize_research_report()`; expanded `_FALSE_REPO_BLOCKERS`; `CURSOR_MODEL` config; tests.
  - Source: `bf6ef22d`, `3e1f17ec`

- **Research crash — parallel `cursor_search` budget race**
  - Symptom: `JSONDecodeError: Expecting value: line 1 column 1 (char 0)` in `budget.py:_load`.
  - Root cause: Concurrent `cursor_search` calls raced on the same budget JSON file; a non-atomic read mid-write returned an empty string.
  - Fix: Per-file `threading.Lock` and atomic temp-file + `replace()` writes in `budget.py`; tests added.
  - Source: `bf6ef22d`

- **`cursor_search` budget-abort test failure**
  - Symptom: `test_cursor_search_budget_abort` failed after the gateway migration.
  - Root cause: Budget pre-check not applied before decrement; shared budget path; `hard_limit` mismatch.
  - Fix: Pre-check via `_budget_path`; added a `budget_dir` param to `make_cursor_search_tool()`; test uses `tmp_path` + `hard_limit=1`.
  - Source: `223c37f8`

- **Duplicate research skill selection in dry-run**
  - Symptom: The skill loader was invoked twice during a dry run.
  - Root cause: `build_research_prompt()` re-resolved the skill even when the CLI had already resolved it.
  - Fix: Added an optional `ticket_skill` param; the CLI passes the pre-resolved skill (and a later fix removed an invalid kwarg + centralized loading in `PromptBuilder`).
  - Source: `dde5f4df`, `ca6d53d0`

---

## 11. Orchestrator, Pipeline & Skills

- **Orchestrator context bloat**
  - Symptom: Routing prompt grew to ~35k+ tokens (full ticket body, plans, `old_string`/`new_string`, gate history).
  - Root cause: The full `AgentState`/ticket was dumped into the routing prompt.
  - Fix: Added `orchestrator_context.py` with `build_orchestrator_routing_context()`; wired into the orchestrator; tests added.
  - Source: `bf6ef22d`

- **Skills failed to load (wrong directory)**
  - Symptom: Orchestrator/planner skills failed to load at runtime.
  - Root cause: Agents pointed at a nonexistent `src/agents/*/skills/` instead of repo-level `skills/<module>/`.
  - Fix: Wired `module_skills_dir()` in the orchestrator/planner; wired a `general_patterns` baseline in `skills_loader.py`.
  - Source: `c12ee848`

- **Skill registry didn't select `java-sast-fixes` for the SQLi mock ticket**
  - Symptom: M1.4 tests failed — the SAST skill wasn't selected.
  - Root cause: `_matches_ticket()` required the language in the summary even when labels matched; missing `sql-injection` trigger label.
  - Fix: Label-match short-circuit in `skill_registry.py`; added trigger labels to the skill.
  - Source: `8d011abc`

- **`skills_loader` ingested `README.md` as a skill**
  - Symptom: The loader picked up `README.md` as a skill file.
  - Root cause: The glob included all `*.md`.
  - Fix: Skip `readme.md` in `skills_loader.py`.
  - Source: `8d011abc`

- **Mock-security pipeline instability (planner JSON / deterministic plan)**
  - Symptom: The planner sometimes returned invalid JSON; the demo pipeline failed intermittently.
  - Root cause: The LLM planner was non-deterministic for the known demo fixture.
  - Fix: Added `build_mock_security_plan()` + a planner fallback; `_reset_mock_security_fixture()` in `runner.py`.
  - Source: `87ac4960`, `a1f55238`

- **Mock-security removal regression (16 failing tests)**
  - Symptom: `16 failed, 199 passed` after removing the SEC-4421 mock-security demo.
  - Root cause: Tests still imported `_mock_jira_ticket`/`mock_security_jira_ticket`, expected `--mock-security`, referenced deleted fixtures, or hit real Jira/LLM without mocks.
  - Fix: Removed mock code paths; added `tests/helpers.py`; rewrote tests across the suite; deleted obsolete `test_m13_sub_agents.py` / `test_orchestrator_loop.py`. Result: `205 passed`.
  - Source: `b94c28c8`

- **M1.3 E2E / orchestrator test assertion mismatches**
  - Symptom: `test_m13_pipeline_wires_three_agents_in_sequence` and `test_pipeline_orchestrator_dummy_loop` failed.
  - Root cause: Tests used the wrong helpers / still expected `plan.execution_steps` rather than `fix_plan.targets`.
  - Fix: Rewrote assertions to use `typed_fix_plan()` / `typed_research()`.
  - Source: `97a9c0ca`

- **Mid-migration pytest failures (skills harness)**
  - Symptom: Multiple failing tests during the SKILL.md migration.
  - Root cause: Incomplete wiring of the new skill layout, schemas, and test expectations.
  - Fix: Added/updated tests (`test_skill_scripts.py`, `test_prompt_builder.py`, `test_validate_plan.py`), script shims, and agent fixes; suite reached green.
  - Source: `ca6d53d0`

- **`test_research_deep_skills` expected the old SKILL.md layout**
  - Symptom: Failed asserting `(SKILL_DIR / "SKILL.md").is_file()`.
  - Root cause: Skills migrated to `research.md` under `skills/research/`.
  - Fix: Updated the test to expect `research.md`.
  - Source: `8d011abc`

- **Various test/credential isolation issues**
  - Symptom: Jira tests hit live Jira / `JiraCredentialsError` not raised; pipeline dry-run tests called real routing.
  - Root cause: Tests didn't clear credentials or mock `fetch_jira_ticket`/`resolve_routing`.
  - Fix: Added `_without_jira_env()`/`_restore_env()` helpers, patched `credentials_configured`, and mocked Jira fetch + routing with a `_FULL_ROUTING` fixture.
  - Source: `2b045552`, `b94c28c8`

- **Research test assertions on model shape**
  - Symptom: `AssertionError` on `report.affected_files[0]["path"]`; `hypotheses` empty validation error.
  - Root cause: Tests used dict access on Pydantic models / built invalid fixtures.
  - Fix: Use attribute access (`.path`); add a minimal hypothesis to the fixture.
  - Source: `87ac4960`, `bf6ef22d`

---

## 12. Chainlit UI & Token Usage

- **Chainlit silent orchestrator EXIT**
  - Symptom: UI showed only "Starting…" then "Workflow completed" with no explanation.
  - Root cause: `exit_message` wasn't populated; no route/phase summary; stale thread checkpoints reused.
  - Fix: Populate `exit_message` from routing reasoning; added `_show_workflow_result()` + per-step progress; fresh `thread_id` per run.
  - Source: `0ac60418`

- **Chainlit missing research results after long runs**
  - Symptom: Terminal showed `[research_deep] complete`, but Gate 1 / research output never appeared in the UI.
  - Root cause: Sync `graph.stream()` blocked Chainlit's async event loop, killing WebSocket updates.
  - Fix: Switched to `graph.astream()` with `asyncio.to_thread()` for the Jira fetch.
  - Source: `6843e681`

- **`SqliteSaver does not support async methods`**
  - Symptom: `Workflow error: The SqliteSaver does not support async methods...`.
  - Root cause: `graph.astream()` requires an async checkpointer, but the app used the sync `SqliteSaver`.
  - Fix: `ensure_async_checkpointer()` in `checkpointing.py`; optional checkpointer in `graph.py`; Chainlit initializes the async saver; added `aiosqlite>=0.20.0`.
  - Source: `6843e681`

- **Misleading Jira description truncation in the UI**
  - Symptom: The UI showed a truncated `description_preview` (500 chars), implying an incomplete fetch.
  - Root cause: Compact logging/UI preview caps; the full text was already in `problem_statement`.
  - Fix: Ingestion message now shows repo, target branch, and labels; clarified raw vs parsed fields.
  - Source: `6843e681`

- **LLM token usage not visible (0 tokens in UI)**
  - Symptom: `LLM token usage: 0 tokens recorded`.
  - Root cause: No `PipelineResult.token_usage`; the graph path never started tracking; a `ContextVar` didn't propagate to LangGraph worker threads.
  - Fix: Added `to_dict()`, a global tracker fallback, `PipelineResult.token_usage`, CLI/Chainlit surfacing, and Sprinklr `response_metadata.usage` parsing.
  - Source: `87ac4960`

---

## 13. Research Agent Token Minimisation (Deep Agent)

### The problem

LangSmith showed the Research Deep Agent's **first LLM call costing ~7.2k input tokens**,
before the agent had read a single repo file — and because it runs a multi-turn ReAct loop,
that fixed overhead was **re-sent on every turn** (×12 tool-call budget → 80k+ tokens/run).

Precise tokenisation (tiktoken `o200k_base`, the GPT-5/Sprinklr encoding) of the actual
call-1 payload proved the ticket context was **not** the culprit:

| Component | Tokens | % | Notes |
|---|---|---|---|
| Tool schemas (13 tools) | 4,814 | 63% | `task` 1664, `write_todos` 982, `read_file` 603 dominated |
| DeepAgents system boilerplate | ~2,043 | 27% | BASE prompt + write_todos + skills + filesystem + task blocks |
| Human (ticket) context | 730 | 10% | The only "real" content — already cheap |

### Argument Point = 
If you are doing this then why need hardcoded tools:- 
It's the load_skill tool doing a plain Python path.read_text(...) (via load_skill_body for research.md). The model just decides which skill to load; the actual file read happens inside our tool.

Then what's the point of making and hardcoding primitive tools means we can simply use these python tools to read. because this can be asked to me as an argument, why did you write cod for own toools indtead of using python FS tools
We use custom domain tools (load_skill, cursor_search) where we need special token/history behavior, and hardened L1 primitives (read/ls/bash) for general repo navigation — because deepagents’ built-in FS tools cannot see the ticket repo and would cost thousands of tokens per call if we used them.

### Root causes

1. **HarnessProfile never matched the model.** The exclusion profile was registered under a
   custom key `"sprinklr-research"`, but deepagents resolves profiles by **model identity**
   (`openai:gpt-5.2-...`). Result: `excluded_tools = []` — every deepagents built-in
   (`task`, `write_todos`, `read_file`, `write_file`, `edit_file`, `glob`, `grep`, `execute`)
   was still sent on every call, on top of our own primitives.
2. **Unused scaffolding shipped by default.** We don't use deepagents subagents or its todo
   list, yet `task` (1,664 tok) and `write_todos` (982 tok) were the two biggest single tools.
3. **`skills=["/"]` backend was dead weight.** Our tree has no `SKILL.md` files (we use
   `research.md` etc.), so deepagents discovered nothing and injected a 411-token
   "(No skills available yet)" block plus a `read_file` tool we paid 603 tok/call for.
4. **ReAct replays everything.** System prompt + all tool schemas + growing history are
   re-sent each turn — so any per-call bloat is multiplied by the number of iterations.

### What we implemented (and why)

Guiding principle: **keep our cheap repo primitives, kill the expensive deepagents
scaffolding, and keep skills progressive** (this is a production security agent that must
scale to many OWASP categories — pre-injecting all skills does not scale).

1. **Fixed the harness profile binding** — register under `openai` and the resolved
   `{provider}:{model}` key so `excluded_tools` actually applies. Also excludes
   `TodoListMiddleware` and disables the general-purpose subagent
   (`GeneralPurposeSubagentProfile(enabled=False)` + `subagents=[]`) to drop `task`.
2. **`ResearchToolFilterMiddleware`** (belt-and-suspenders) — a `wrap_model_call` filter
   that lets only research tools reach the model regardless of profile resolution. Removes
   `task`, `write_todos`, `read_file`, and all FS-mutation duplicates.
3. **Removed the `skills=` backend** — drops the broken `SKILL.md` discovery, the
   "(No skills available yet)" block, and the `read_file` tool.
4. **Progressive tool disclosure** — call 1 exposes only `load_skill`, `cursor_search`,
   `read`, `ls`, `bash`; `web_search` and `repo_profile` unlock only **after** the first
   `cursor_search` returns. Keeps each prompt's schema footprint minimal.
5. **Progressive skill loading via `load_skill` + `ProgressiveSkillMiddleware`** (replaces
   `read_file`). The model calls `load_skill("injection")`; the middleware intercepts the
   tool result, stores the full body in a per-run `ResearchRunContext`, **injects it into the
   system prompt**, and replaces the bulky `ToolMessage` in history with a ~15-token ack.
   So the skill body is **never duplicated in replayed history**, and the `load_skill` schema
   (~80 tok) disappears after the skill is loaded. This preserves progressive disclosure
   without `read_file`'s 603-tok/call schema or its 2.5k-tok ToolMessage replayed forever.
6. **Shorter tool descriptions** — trimmed primitive (`read`/`ls`/`bash`) and agent-specific
   (`cursor_search`/`web_search`/`repo_profile`) descriptions; full args stay in `args_schema`.
7. **Removed a stray `print()`** in `research_agent.py`.

### Files changed

- `src/agents/research/middleware.py` *(new)* — `ResearchToolFilterMiddleware`,
  `ProgressiveSkillMiddleware`, tool-visibility + history-compaction logic.
- `src/agents/research/run_context.py` *(new)* — per-run `loaded_skills` store shared by
  the two middlewares.
- `src/agents/research/tools/load_skill.py` *(new)* — lightweight progressive skill loader
  tool (resolves `research` + `security/*` skills).
- `src/agents/research/deep_runner.py` — harness-profile fix, `subagents=[]`, no `skills=`,
  research-only system prompt, compact `load_skill` catalog in the user message.
- `src/agents/research/tools/builder.py` — wires `load_skill`, short primitive descriptions.
- `src/agents/research/research_agent.py` — drop debug print; simplified `build_research_agent`.
- `src/agents/research/tools/{cursor_search,web_search,langchain_tools}.py` — short descriptions.
- `src/core/tool_permissions.yaml` — added `load_skill` to research's allowed tools.
- Tests: `tests/test_research_middleware.py` *(new)*, updated `tests/test_research_deep_skills.py`.

### Measured impact

Call-1 input dropped from **~7,587 tokens → ~2,500 tokens** (text + 4–5 core tool schemas),
with tool-schema cost cut from **4,814 → ~450–900**. Because the savings are per-call, the
full-run reduction is far larger (tens of thousands of tokens). All research-related tests
pass (47 incl. new middleware coverage).

### Still open (Phase 3, not yet implemented)

- **History compression** of large `cursor_search`/`read` tool outputs after N calls.
- **Prompt caching** on the Sprinklr router (deepagents only wires Anthropic caching, a
  no-op for our `ChatOpenAI`-based Sprinklr model) to cheapen the repeated system+tool prefix.

---

## Notable Unresolved / Environmental Issues

These came up repeatedly but were diagnosed as environment/config issues or were left as
proposals rather than implemented fixes:

- **OpenRouter 429 — `free-models-per-day` rate limit.** Quota exhaustion on free models; advised to wait / add credits / switch provider. (`dde5f4df`, `6843e681`, `a1f55238`, `55b40e47`)
- **OpenRouter HTTP 500 (Internal Server Error)** during research — only debug instrumentation added before the session pivoted. (`5774b30a`)
- **Sprinklr router 403 off VPN / corporate network** — error messaging improved, but the gateway block is environmental. (`98ee441a`, `87ac4960`)
- **`test_executor_cli_runs_fixture`** — fails in the sandbox because it invokes the real Cursor SDK (`internal: internal error`). (`af4a6399`, `6fbcbb07`)
- **Edit no-op `replacement produced identical content`** (worktree/ticket-repo mismatch → truncated patch → fallback `old==new`) — fixes proposed only. (`b4cd9ee5`, `62b333e6`)
- **Executor full-file completeness + PostgreSQL intent heuristics** (`%s` / `%(name)s` not recognized) — diagnosed, fix offered but not implemented. (`431d2e22`)
- **`test_token_usage`, `test_sprinklr_llm`, `test_git_worktree`** — pre-existing failures noted as unrelated to the session work. (`ca6d53d0`, `5343a0fb`, `b94c28c8`)
- **Cursor agent stuck in dry-run (`has_api_key=False`)** — missing `CURSOR_API_KEY` (user config). (`b94c28c8`)
