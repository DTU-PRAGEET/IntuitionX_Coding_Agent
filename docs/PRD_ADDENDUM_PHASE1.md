# PRD Addendum — Phase 1 Scope Deviations

**Base document:** Sprinklr AI Coding Agent System PRD v2.0  
**Effective:** Phase 1 MVP  
**Purpose:** Document intentional deviations so implementation does not conflict with the base PRD.

---

## 1. Execution Model: Generic Tools (Not Cursor CLI)

**PRD v2.0:** Implementation Agent uses `cursor_open_repo`, `cursor_edit_file`.

**Phase 1:** Pi-style generic tools — `read_file`, `write_file`, `edit_file` (patch primitive), `grep`, `glob_files`, `list_dir`, `bash_command`.

**Rationale:** Lower integration complexity; aligns with industry practice (Pi, Claude Code). Cursor CLI deferred to Future Work.

---

## 2. Routing: MICRO Route with Quick Confirm

**PRD v2.0:** Research bypass routes to Planning only. Never implement without human-approved plan.

**Phase 1:** Four routes:
- `FULL` — Research → Gate 1 → Plan → Gate 2 → Executor
- `SKIP_RESEARCH` — Plan → Gate 2 → Executor
- `MICRO` — Micro-plan (1–3 steps) → Quick Confirm → Executor
- `EXIT` — DONE / invalid tickets

**MICRO eligibility:** Single/max-2 named files, no auth/architecture keywords, no previous failures, LOW risk, no create_files/write_tests.

**Rationale:** Fast path for trivial fixes while preserving human visibility via quick confirm.

---

## 3. Code Review: Human MR Review Only

**PRD v2.0:** Code Review Agent (inline LLM) as quality gate before MR.

**Phase 1:** Diff preview in Chainlit; developer reviews GitLab MR manually. No LLM code review agent.

---

## 4. Test Execution and Build Verification

**PRD v2.0:** Plans may include `write_tests: true`; CI/CD covers build/test stages.

**Phase 1:** Agents do not run tests or Gradle/build verification. `compilation_status: SKIPPED` in execution output.

**Rationale:** Polyglot support deferred; reduces executor complexity for MVP.

---

## 5. RAG / Knowledge Base

**PRD v2.0:** Phase 2 includes Context Collection and Knowledge Base / RAG Agent.

**Phase 1:** Research uses signal-driven grep/glob discovery + web search. Full RAG deferred to Phase 2.

---

## 6. HITL Surface: Chainlit

**PRD v2.0:** Slack, email, or UI.

**Phase 1:** Chainlit chat UI with LangGraph `interrupt` for Gate 1, Gate 2, and MICRO quick confirm.

---

## 7. CI/CD Self-Healing

**PRD v2.0:** Post-MVP automated webhook listener.

**Phase 1:** Manual error log upload via `POST /webhook/cicd-error`. Re-orchestrates through Research → Plan → Execute.

---

## 8. GitLab Target Branch

**PRD v2.0:** Implied `develop` as integration branch.

**Phase 1:** `GIT_TARGET_BRANCH=develop` (configurable). MRs target `develop`, not `main`.

---

## 9. Branch Naming

**PRD v2.0:** `fix/JIRA-ID-short-description` or `feat/JIRA-ID-short-description`.

**Phase 1:** Implemented per PRD. Max 50 characters, lowercase, hyphenated.

---

## 10. Jira Context Extractor

**PRD v2.0:** Full extractor with history, linked issues, attachment download.

**Phase 1:** Tier 1 + Tier 2 — core fields, acceptance criteria extraction, compressed comments, repo_links, linked issue summaries. Tier 3 (deep history, attachment download, vision) deferred to Phase 2.

---

## Schema Alignment

Phase 1 `AgentState` uses PRD-aligned schemas:
- `ResearchOutput` — hypotheses with evidence, affected_files with evidence_strength
- `PlanOutput` — execution_steps with LOCATE/EDIT/VERIFY/SEARCH actions
- `routing_route` — FULL | SKIP_RESEARCH | MICRO | EXIT

---

## Features Explicitly Deferred

| Feature | Target Phase |
|---|---|
| RAG / Knowledge Base | Phase 2 |
| AST / tree-sitter call graphs | Phase 2 |
| Gradle compile verification | Phase 2 |
| Test execution | Phase 2 |
| LLM Code Review Agent | Phase 2 |
| Slack / Jira notifications | Phase 2 |
| Image / vision analysis | Phase 2 |
| Automated CI/CD webhooks | Phase 2 |
| K8s production deployment | Phase 2 |
| Cursor CLI integration | Future Work |
