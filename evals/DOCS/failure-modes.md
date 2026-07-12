# Failure Mode Taxonomy — Integration Eval Baseline (Milestone 2.4)

> **Status:** Current baseline — Pass 3 (`run_030`, 100% pass rate, 2026-07-11)  
> **Golden suite:** `G-01` … `G-18` (18 cases across 5 vulnerable repos)  
> **Eval mode:** Full pipeline integration (`routing → research → planner → executor`)  
> **Pass criterion:** Executor judge `passed: true` and no `stage_failed` in pipeline artifact

---

# Test_Case Category =

| Category | Cases | Count |
|----------|-------|------:|
| **Too Specific** | G-04, G-06, G-07, G-08, G-09, G-17, G-18 | **7** |
| **Moderately Specific** | G-01, G-03, G-05, G-10, G-12, G-13, G-14 | **7** |
| **Vague / Realistic(Not very Clear)** | G-02, G-11, G-15, G-16 | **4** |

## 1. Executive Summary

This document records the **end-to-end integration eval baseline** for the Sprinklr Jira Solver agent pipeline. Three benchmark passes were executed on the same 18 golden cases:

| Pass | Runs | Date | Experiment | Overall Pass Rate |
|------|------|------|------------|-------------------|
| **Pass 1 — Pre-validation** | `run_007` + `run_010` | 2026-07-04 | `mixed-10-unit` | **3 / 18 (16.7%)** |
| **Pass 2 — Post-validation** | `mixed_15_afterSemanticANDValidationChanges` + `mixed_3_afterSemanticANDVlidationChanges` | 2026-07-06 | `mixed-15-unit-ChangingValidationLayerAndSemanticInstructions` | **13 / 18 (72.2%)** |
| **Pass 3 — Skills improvements** | `run_030` | 2026-07-11 | `Skills-adjustment-Test3` | **18 / 18 (100%)** |

**Net improvement (Pass 1 → Pass 2):** +55.5 pp after semantic-targeting and validation-layer changes.  
**Net improvement (Pass 2 → Pass 3):** +27.8 pp after planner/executor/research skill improvements — all 5 remaining failures recovered.

Pass 1 was split across two runs (`run_007`: G-01,03,05,06,08,10,14,17; `run_010`: G-02,04,07,09,11–13,15,16,18). Pass 2 was similarly split (`mixed_15`: G-01–15; `mixed_3`: G-15–18). Pass 3 ran all 18 cases in a single run (`run_030`).

All intermediate outputs (research reports, plans, plan validation, execution steps, git diffs, ±50-line code context) are frozen in `evals/runs/<run_id>/artifacts/`. LLM judge verdicts are in `evals/runs/<run_id>/judge_results/{planner,executor}/`.

---

## 2. Failure Mode Taxonomy

Every failure is classified into exactly one of five modes defined in Milestone 2.4. A case **passes** only when the executor judge approves the final patch and the pipeline completes without a stage failure.

### 2.1 Mode Definitions

| Mode | Code | Definition | Typical Pipeline Stage |
|------|------|------------|------------------------|
| **Wrong File** | `WRONG_FILE` | Agent identified a vulnerability but edited the wrong file(s) — none of the golden `expected_files` were touched. | Research / Planner |
| **Wrong Fix Type** | `WRONG_FIX_TYPE` | Correct file targeted, but the remediation approach does not match the required fix pattern (e.g., wrapped secret in placeholder instead of removing it, over-broad auth decorator). | Planner / Executor |
| **Broken Syntax** | `BROKEN_SYNTAX` | Correct intent and file, but generated code is incomplete, syntactically invalid, or fails executor edit application. | Executor |
| **Partial Fix** | `PARTIAL_FIX` | A patch was applied to the right area but the vulnerability is not fully closed, or scope creep introduces regressions that undermine the fix. | Executor |
| **No Output** | `NO_OUTPUT` | Pipeline timed out, errored, or aborted before producing a judgeable patch — most commonly planner semantic validation rejection. | Planner (pre-executor) |

#### Sub-patterns observed

| Sub-pattern | Parent Mode | Description |
|-------------|-------------|-------------|
| `SEMANTIC_VALIDATION_REJECT` | `NO_OUTPUT` | Planner produced EDIT steps whose semantic targets (`target_name`, `target_type`, `occurrence_index`) could not be resolved in the repo snapshot. Pipeline aborts before executor runs. |
| `EDIT_INCOMPLETE` | `BROKEN_SYNTAX` | Executor LLM returned a truncated or syntactically broken `new_string`; edit applier rejected the patch. |
| `VERIFY_FAIL` | `BROKEN_SYNTAX` / `PARTIAL_FIX` | Edit applied but post-edit VERIFY step could not find the `intent_marker` in the file. |
| `SCOPE_CREEP` | `PARTIAL_FIX` / `WRONG_FIX_TYPE` | Correct primary fix plus unrelated file changes (e.g., SQLi fix bundled into a BAC ticket). |
| `COSMETIC_REMEDIATION` | `WRONG_FIX_TYPE` | Surface-level change that preserves the underlying vulnerability (e.g., `${PASSWORD:hardcoded}` keeps secret in VCS). |

---

## 3. Pass 1 — Pre-Validation Baseline (`run_007` + `run_010`)

### 3.1 Scorecard

#### Overall

| Metric | Value |
|--------|-------|
| Cases run | 18 / 18 |
| End-to-end pass | **3** |
| End-to-end fail | **15** |
| **Pass rate** | **16.7%** |

#### By Vulnerability Class

| Vuln Class | Pass | Total | Rate | Failed Cases |
|------------|------|-------|------|--------------|
| Injection | 1 | 3 | 33% | G-03, G-10 |
| IDOR | 1 | 2 | 50% | G-12 |
| Misconfig | 1 | 2 | 50% | G-04 |
| Auth | 0 | 4 | 0% | G-08, G-09, G-11, G-16 |
| BAC | 0 | 3 | 0% | G-05, G-13, G-15 |
| XSS | 0 | 2 | 0% | G-03, G-14 |
| Crypto | 0 | 1 | 0% | G-02 |
| Secrets | 0 | 1 | 0% | G-17 |

#### By Language

| Language | Pass | Total | Rate |
|----------|------|-------|------|
| Python | 2 | 9 | 22% |
| Java | 0 | 5 | 0% |
| TypeScript | 0 | 3 | 0% |
| YAML | 1 | 1 | 100% |

#### By Difficulty

| Difficulty | Pass | Total | Rate |
|------------|------|-------|------|
| Easy | 2 | 7 | 29% |
| Medium | 1 | 9 | 11% |
| Hard | 0 | 2 | 0% |

#### Failure Mode Distribution

| Mode | Count | % of Failures |
|------|-------|---------------|
| `NO_OUTPUT` | 6 | 40% |
| `WRONG_FIX_TYPE` | 5 | 33% |
| `BROKEN_SYNTAX` | 3 | 20% |
| `PARTIAL_FIX` | 1 | 7% |

### 3.2 Per-Case Failure Analysis

| Case | Repo | Vuln | Lang | Diff | Result | Failure Mode | Root Cause Summary |
|------|------|------|------|------|--------|--------------|-------------------|
| **G-01** | DVPWA | Injection | python | easy | ✅ PASS | — | Executor parameterized `Student.create` in `sqli/dao/student.py` despite planner only planning VERIFY steps. Scope creep in `sqli/views.py` but core SQLi fixed. |
| **G-02** | DVPWA | Crypto | python | easy | ❌ FAIL | `BROKEN_SYNTAX` | Executor edit to `sqli/dao/user.py` truncated mid-bcrypt implementation. `Edit incomplete: modified snippet shows start of bcrypt branch but...` Pipeline stopped at executor step 2. |
| **G-03** | DVPWA | XSS | python | medium | ❌ FAIL | `NO_OUTPUT` | Planner semantic validation rejected step 2: `could not resolve function 'setup_jinja' in sqli/app.py`. No executor output produced. |
| **G-04** | DVPWA | Misconfig | python | easy | ❌ FAIL | `NO_OUTPUT` | Planner semantic validation rejected step 11: `could not resolve function '__main__' in run.py`. |
| **G-05** | DVPWA | BAC | python | hard | ❌ FAIL | `WRONG_FIX_TYPE` | Added `@authorize(ensure_admin=True)` to entire `students`/`courses` handlers, gating GET as well as POST. Pass criteria require preserving public GET while gating privileged POSTs only. |
| **G-06** | VAmPI | IDOR | python | easy | ✅ PASS | — | Correct ownership check added to `api_views/users.py` password-change endpoint. |
| **G-07** | VAmPI | Injection | python | easy | ❌ FAIL | `PARTIAL_FIX` | SQLi fixed in `models/user_model.py` via ORM, but unnecessary changes to `api_views/users.py` and judge flagged incomplete remediation scope. |
| **G-08** | VAmPI | Auth | python | medium | ❌ FAIL | `NO_OUTPUT` | Planner semantic validation: `could not resolve assignment 'SECRET_KEY' in config.py`. |
| **G-09** | VAmPI | Auth | python | easy | ❌ FAIL | `WRONG_FIX_TYPE` | Unified login error messages (correct intent) but judge found the fix did not fully eliminate enumeration vectors or address server-side verification gaps. |
| **G-10** | WebGoat | Injection | java | medium | ❌ FAIL | `WRONG_FIX_TYPE` | Parameterized SQL in target file but also modified unrelated lesson files (`SqlInjectionLesson5b`, `SqlInjectionLesson6a`). WebGoat training-app context caused over-broad edits. |
| **G-11** | WebGoat | Auth | java | medium | ❌ FAIL | `NO_OUTPUT` | Planner semantic validation: `could not resolve method 'vote' in JWTVotesEndpoint.java` (wrong target in multi-file JWT lesson). |
| **G-12** | WebGoat | IDOR | java | medium | ❌ FAIL | `BROKEN_SYNTAX` | Executor edit to `IDORViewOtherProfile.java` incomplete — authorization guard snippet truncated. |
| **G-13** | WebGoat | BAC | java | hard | ❌ FAIL | `WRONG_FIX_TYPE` | Path traversal mitigation applied (`FilenameUtils.getName` + canonical path check) but judge found remaining bypass vectors and insufficient validation of resolved path. |
| **G-14** | Juice Shop | XSS | typescript | medium | ❌ FAIL | `NO_OUTPUT` | Planner semantic validation: `target 'this.searchValue' (assignment) must be unique...found 2 matches`. Angular component property targeting failed. |
| **G-15** | Juice Shop | BAC | typescript | medium | ❌ FAIL | `NO_OUTPUT` | Planner semantic validation: `could not resolve function 'isAdmin' in lib/insecurity.ts`; import target ambiguity on `security`. |
| **G-16** | Juice Shop | Auth | typescript | medium | ❌ FAIL | `BROKEN_SYNTAX` | Executor edit to `routes/verify.ts` only added imports without implementing algorithm pinning. `Edit incomplete`. |
| **G-17** | WrongSecrets | Secrets | java | easy | ❌ FAIL | `WRONG_FIX_TYPE` | Changed `password=` to `${PASSWORD:ThisEnvironmentIsAnotherPlaceToHide}` — plaintext secret preserved as Spring default value. Cosmetic remediation, not secrets removal. |
| **G-18** | WrongSecrets | Misconfig | yaml | medium | ✅ PASS | — | Converted `k8s/secrets-secret.yml` to SealedSecret with `encryptedData`. Also touched okteto mirror. |

### 3.3 Pass 1 — Key Findings

1. **Planner semantic validation was the #1 blocker** — 6/15 failures (40%) never reached the executor. TypeScript/Angular cases (G-14, G-15) and multi-function Python files (G-03, G-04, G-08) were hardest to target.
2. **Java and TypeScript were 0% pass rate** — all 8 non-Python cases except G-18 failed.
3. **Executor edit truncation** — 3 cases failed because the LLM produced incomplete code blocks (G-02, G-12, G-16).
4. **"Already fixed" planner hypothesis** — G-01 planner scored 35 (fail) for planning VERIFY-only steps while executor still produced a valid fix.

---

## 4. Pass 2 — Post-Validation Baseline

> Changes applied between passes: semantic targeting instructions in planner skill, stricter plan validation, executor edit validation layer, and improved VERIFY semantics.

### 4.1 Scorecard

#### Overall

| Metric | Value |
|--------|-------|
| Cases run | 18 / 18 |
| End-to-end pass | **13** |
| End-to-end fail | **5** |
| **Pass rate** | **72.2%** |
| Δ vs Pass 1 | **+55.5 pp** |

#### By Vulnerability Class

| Vuln Class | Pass 1 | Pass 2 | Δ | Pass 2 Failed |
|------------|--------|--------|---|---------------|
| Injection | 33% | **100%** | +67 pp | — |
| Crypto | 0% | **100%** | +100 pp | — |
| Secrets | 0% | **100%** | +100 pp | — |
| Auth | 0% | **75%** | +75 pp | G-16 |
| BAC | 0% | **67%** | +67 pp | G-12 |
| IDOR | 50% | 50% | — | G-12 |
| XSS | 0% | **50%** | +50 pp | G-14 |
| Misconfig | 50% | **0%** | −50 pp | G-04, G-18 |

#### By Language

| Language | Pass 1 | Pass 2 | Δ |
|----------|--------|--------|---|
| Python | 22% | **89%** | +67 pp |
| Java | 0% | **80%** | +80 pp |
| TypeScript | 0% | **33%** | +33 pp |
| YAML | 100% | **0%** | −100 pp |

#### By Difficulty

| Difficulty | Pass 1 | Pass 2 | Δ |
|------------|--------|--------|---|
| Easy | 29% | **86%** | +57 pp |
| Medium | 11% | **44%** | +33 pp |
| Hard | 0% | **100%** | +100 pp |

#### Failure Mode Distribution

| Mode | Pass 1 | Pass 2 | Δ |
|------|--------|--------|---|
| `NO_OUTPUT` | 6 | **0** | −6 ✅ |
| `WRONG_FIX_TYPE` | 5 | **0** | −5 ✅ |
| `BROKEN_SYNTAX` | 3 | **1** | −2 |
| `PARTIAL_FIX` | 1 | **4** | +3 |
| `PASS` | 3 | **13** | +10 |

### 4.2 Per-Case Failure Analysis

| Case | Repo | Vuln | Lang | Diff | Result | Failure Mode | Root Cause Summary |
|------|------|------|------|------|--------|--------------|-------------------|
| **G-01** | DVPWA | Injection | python | easy | ✅ PASS | — | Clean parameterization in `sqli/dao/student.py` only. Planner now produces EDIT steps. Score: P93 / E96. |
| **G-02** | DVPWA | Crypto | python | easy | ✅ PASS | — | bcrypt password hashing in `sqli/dao/user.py`. Score: P78 / E86. |
| **G-03** | DVPWA | XSS | python | medium | ✅ PASS | — | Enabled autoescape in `sqli/app.py` + template fixes. Planner failed (52) but executor succeeded. Score: P52 / E88. |
| **G-04** | DVPWA | Misconfig | python | easy | ❌ FAIL | `BROKEN_SYNTAX` | Edits applied to `sqli/app.py` and `run.py` but VERIFY step failed: `intent_marker not found in sqli/schema/config.py`. Pipeline continued for judge but no executor verdict. |
| **G-05** | DVPWA | BAC | python | hard | ✅ PASS | — | Selective `@authorize` on POST handlers only. Score: P76 / E93. |
| **G-06** | VAmPI | IDOR | python | easy | ✅ PASS | — | Ownership check on password change. Score: P92 / E96. |
| **G-07** | VAmPI | Injection | python | easy | ✅ PASS | — | ORM query in `models/user_model.py`. Score: P62 / E92. |
| **G-08** | VAmPI | Auth | python | medium | ✅ PASS | — | `SECRET_KEY` sourced from environment in `config.py`. Score: P92 / E95. |
| **G-09** | VAmPI | Auth | python | easy | ✅ PASS | — | Generic login error message. Score: P92 / E92. |
| **G-10** | WebGoat | Injection | java | medium | ✅ PASS | — | PreparedStatement with bound parameter. Score: P78 / E78. |
| **G-11** | WebGoat | Auth | java | medium | ✅ PASS | — | JWT algorithm enforcement in `JWTRefreshEndpoint.java`. Score: P58 / E82. |
| **G-12** | WebGoat | IDOR | java | medium | ❌ FAIL | `PARTIAL_FIX` | Added `userId == authUserId` guard but restructured control flow and modified `IDORViewOwnProfile.java`, `UserProfile.java` beyond plan. Blocks all cross-user access rather than proper authorization. Score: P58 / E32. |
| **G-13** | WebGoat | BAC | java | hard | ✅ PASS | — | Path canonicalization in `ProfileUploadBase.java`. Score: P92 / E78. |
| **G-14** | Juice Shop | XSS | typescript | medium | ❌ FAIL | `PARTIAL_FIX` | Removed `bypassSecurityTrustHtml` from `filterTable` but template still uses `[innerHTML]="searchValue"`. XSS sink remains. Score: P72 / E35. |
| **G-15** | Juice Shop | BAC | typescript | medium | ✅ PASS | — | Server-side `isAdmin` middleware in `lib/insecurity.ts` + route guards in `server.ts` (different files than golden expected, but judge approved). Score: P86 / E96. |
| **G-16** | Juice Shop | Auth | typescript | medium | ❌ FAIL | `PARTIAL_FIX` | Added `algorithms:['RS256']` to jwt calls but broke `verify()` by using async callback API in a synchronous boolean function — returns false for all tokens. Score: P86 / E35. |
| **G-17** | WrongSecrets | Secrets | java | easy | ✅ PASS | — | Secret removed from `application.properties`, sourced via `${PASSWORD}` env var without default. Score: P58 / E82. |
| **G-18** | WrongSecrets | Misconfig | yaml | medium | ❌ FAIL | `PARTIAL_FIX` | SealedSecret conversion directionally correct but final YAML corrupted (`apiVersion: bitnami.com/v1alpha1\n    3| metadata:`). Invalid manifests would fail `kubectl apply`. Score: P82 / E35. |

### 4.3 Pass 2 — Key Findings

1. **Semantic validation failures eliminated** — all 6 `NO_OUTPUT` planner rejections from Pass 1 now produce executor output. This accounts for the majority of the +55.5 pp improvement.
2. **Remaining failures are quality-depth issues** — all 5 Pass 2 failures are `PARTIAL_FIX` (4) or `BROKEN_SYNTAX` (1), meaning the pipeline now consistently reaches the executor but struggles with fix completeness.
3. **TypeScript is the new weak point** — 2/3 TypeScript cases still fail (G-14 DOM XSS requires template + component co-change; G-16 async/sync API mismatch).
4. **Multi-file XSS and YAML remain hard** — G-14 needs both `.ts` and `.html` changes; G-18 YAML corruption suggests executor struggles with declarative config formats.

---

## 5. Pass 3 — Skills Improvements Baseline (`run_030`)

> Changes applied between Pass 2 and Pass 3: planner, executor, and research skill updates (multi-file fix planning, sync API awareness, minimal IDOR guards, config/YAML edit guidance, validation policy refinements).

### 5.1 Scorecard

#### Overall

| Metric | Value |
|--------|-------|
| Cases run | 18 / 18 |
| End-to-end pass | **18** |
| End-to-end fail | **0** |
| **Pass rate** | **100%** |
| Δ vs Pass 2 | **+27.8 pp** |
| Planner judge mean | **94.9** |
| Executor judge mean | **96.8** |

#### By Vulnerability Class

| Vuln Class | Pass 2 | Pass 3 | Δ |
|------------|--------|--------|---|
| Injection | 100% | **100%** | — |
| Crypto | 100% | **100%** | — |
| Secrets | 100% | **100%** | — |
| Auth | 75% | **100%** | +25 pp |
| BAC | 67% | **100%** | +33 pp |
| IDOR | 50% | **100%** | +50 pp |
| XSS | 50% | **100%** | +50 pp |
| Misconfig | 0% | **100%** | +100 pp |

#### By Language

| Language | Pass 2 | Pass 3 | Δ |
|----------|--------|--------|---|
| Python | 89% | **100%** | +11 pp |
| Java | 80% | **100%** | +20 pp |
| TypeScript | 33% | **100%** | +67 pp |
| YAML | 0% | **100%** | +100 pp |

#### Failure Mode Distribution

| Mode | Pass 2 | Pass 3 | Δ |
|------|--------|--------|---|
| `PASS` | 13 | **18** | +5 |
| `PARTIAL_FIX` | 4 | **0** | −4 ✅ |
| `BROKEN_SYNTAX` | 1 | **0** | −1 ✅ |
| `NO_OUTPUT` | 0 | 0 | — |
| `WRONG_FIX_TYPE` | 0 | 0 | — |

### 5.2 Recovered Cases (Pass 2 failures → Pass 3 passes)

| Case | Repo | Vuln | Lang | Pass 2 Mode | Pass 3 Scores | Root Cause Fix Summary |
|------|------|------|------|-------------|---------------|------------------------|
| **G-04** | DVPWA | Misconfig | python | `BROKEN_SYNTAX` | P96 / E100 | Config schema + `sqli/app.py` + `run.py` aligned; debug defaults False, log_level config-driven |
| **G-12** | WebGoat | IDOR | java | `PARTIAL_FIX` | P97 / E98 | Minimal `userId.equals(authUserId)` guard; removed hardcoded victim ID; no control-flow restructure |
| **G-14** | Juice Shop | XSS | typescript | `PARTIAL_FIX` | P98 / E98 | Component: removed `bypassSecurityTrustHtml`; template: `[innerHTML]` → `{{ searchValue }}` |
| **G-16** | Juice Shop | Auth | typescript | `PARTIAL_FIX` | P100 / E100 | Sync `jwt.verify(token, publicKey, { algorithms: ['RS256'] })`; boolean contract preserved |
| **G-18** | WrongSecrets | Misconfig | yaml | `PARTIAL_FIX` | P92 / E70 | Plain Secret manifests deleted; scripts updated to SealedSecret workflow (soft failures on missing manifest files) |

### 5.3 Pass 3 — Key Findings

1. **All Pass 2 failure modes eliminated** — zero `PARTIAL_FIX` or `BROKEN_SYNTAX` failures remain.
2. **TypeScript recovered fully** — G-14 (multi-file component+template) and G-16 (sync JWT verify) both pass.
3. **YAML/Misconfig recovered** — G-04 and G-18 now pass; G-18 passes with executor score 70 and documented soft failures (missing `sealed-funnystuff.json` in diff).
4. **Judge means improved** — planner 77.4 → 94.9 (+17.5); executor 75.9 → 96.8 (+20.9).

---

## 6. Cross-Pass Comparison Matrix

| Case | Pass 1 | Pass 2 | Pass 3 | Mode (P1) | Mode (P2) | Notes |
|------|--------|--------|--------|-----------|-----------|-------|
| G-01 | ✅ | ✅ | ✅ | — | — | Stable pass |
| G-02 | ❌ | ✅ | ✅ | `BROKEN_SYNTAX` | — | bcrypt complete |
| G-03 | ❌ | ✅ | ✅ | `NO_OUTPUT` | — | Semantic targeting resolves `setup_jinja` |
| G-04 | ❌ | ❌ | ✅ | `NO_OUTPUT` | `BROKEN_SYNTAX` | **Recovered P3:** config-driven debug/log_level |
| G-05 | ❌ | ✅ | ✅ | `WRONG_FIX_TYPE` | — | Selective POST-only auth |
| G-06 | ✅ | ✅ | ✅ | — | — | Stable pass |
| G-07 | ❌ | ✅ | ✅ | `PARTIAL_FIX` | — | ORM fix accepted |
| G-08 | ❌ | ✅ | ✅ | `NO_OUTPUT` | — | `SECRET_KEY` env-sourced |
| G-09 | ❌ | ✅ | ✅ | `WRONG_FIX_TYPE` | — | Generic login error |
| G-10 | ❌ | ✅ | ✅ | `WRONG_FIX_TYPE` | — | PreparedStatement |
| G-11 | ❌ | ✅ | ✅ | `NO_OUTPUT` | — | JWT endpoint targeted |
| G-12 | ❌ | ❌ | ✅ | `BROKEN_SYNTAX` | `PARTIAL_FIX` | **Recovered P3:** minimal IDOR guard |
| G-13 | ❌ | ✅ | ✅ | `WRONG_FIX_TYPE` | — | Path canonicalization |
| G-14 | ❌ | ❌ | ✅ | `NO_OUTPUT` | `PARTIAL_FIX` | **Recovered P3:** component + template XSS co-fix |
| G-15 | ❌ | ✅ | ✅ | `NO_OUTPUT` | — | Server-side `isAdmin` middleware |
| G-16 | ❌ | ❌ | ✅ | `BROKEN_SYNTAX` | `PARTIAL_FIX` | **Recovered P3:** sync `jwt.verify` RS256 pinning |
| G-17 | ❌ | ✅ | ✅ | `WRONG_FIX_TYPE` | — | Secret externalized |
| G-18 | ✅ | ❌ | ✅ | — | `PARTIAL_FIX` | **Recovered P3:** plain Secret removed (E70, soft failures) |

**Pass 3 result:** 18/18 pass. G-18 passes with executor score 70 — SealedSecret manifests not visible in diff.

---

## 7. Failure Mode Deep Dives

### 7.1 `NO_OUTPUT` — Pipeline Abort Before Executor (Pass 1: 6 cases)

The dominant Pass 1 failure mode. All instances trace to **planner semantic EDIT step validation** in `src/agents/planner/validation.py`:

```
ValueError: Planner produced invalid semantic EDIT steps:
  step N: could not resolve <target_type> '<target_name>' in <file>
```

| Case | Rejection Detail | Why It Happened |
|------|-----------------|-----------------|
| G-03 | `could not resolve function 'setup_jinja'` | Jinja setup uses inline call, not a named function |
| G-04 | `could not resolve function '__main__'` | `run.py` guard block not a resolvable function target |
| G-08 | `could not resolve assignment 'SECRET_KEY'` | Module-level assignment in `config.py` not uniquely matched |
| G-11 | `could not resolve method 'vote'` | Planner targeted wrong JWT file (`JWTVotesEndpoint` vs `JWTRefreshEndpoint`) |
| G-14 | `target 'this.searchValue' must be unique, found 2 matches` | Angular component property assigned in multiple methods |
| G-15 | `could not resolve function 'isAdmin'`; import ambiguity | `isAdmin` is inline logic, not a standalone function |

**Resolution (Pass 2):** Improved semantic targeting instructions + relaxed occurrence resolution. All 6 cases now produce executor output.

### 7.2 `WRONG_FIX_TYPE` — Correct File, Wrong Approach (Pass 1: 5 cases)

| Case | Expected Fix | Actual Fix | Gap |
|------|-------------|------------|-----|
| G-05 | Gate POST only, preserve GET | `@authorize(ensure_admin=True)` on entire handler | Over-restrictive access control |
| G-09 | Eliminate enumeration | Unified error string | Judge: residual enumeration vectors |
| G-10 | Parameterize one lesson file | Modified 3 lesson files | Training-app scope creep |
| G-13 | Canonical path check | Partial path sanitization | Remaining traversal bypass |
| G-17 | Remove secret from VCS | `${PASSWORD:secret}` placeholder | Secret still committed as default |

### 7.3 `BROKEN_SYNTAX` — Invalid or Incomplete Code (Pass 1: 3, Pass 2: 1)

| Case | Pass | Symptom |
|------|------|---------|
| G-02 | 1 | bcrypt implementation truncated mid-function |
| G-12 | 1 | Authorization guard `if` block incomplete |
| G-16 | 1 | Only imports added, no algorithm pinning logic |
| G-04 | 2 | VERIFY_FAIL — intent marker missing from `sqli/schema/config.py` |

### 7.4 `PARTIAL_FIX` — Fix Applied, Vulnerability Open (Pass 1: 1, Pass 2: 4)

| Case | Pass | What Was Fixed | What Remains |
|------|------|---------------|--------------|
| G-07 | 1 | ORM query in `user_model.py` | Unnecessary `api_views/users.py` changes |
| G-12 | 2 | `userId == authUserId` check added | Wrong control-flow restructuring; blocks lesson behavior |
| G-14 | 2 | Removed `bypassSecurityTrustHtml` | `[innerHTML]="searchValue"` still in template |
| G-16 | 2 | `algorithms:['RS256']` added | `verify()` broken — async callback in sync function |
| G-18 | 2 | SealedSecret conversion | YAML `apiVersion` line corrupted, manifests invalid |

---

## 8. Remediation Priority Queue

> **Status (Pass 3):** All P0/P1 items from Pass 2 analysis are resolved. Pass 3 achieved **18/18 (100%)**.

Remaining quality caveats (non-blocking — cases still pass):

| Priority | Target | Case | Notes |
|----------|--------|------|-------|
| P2 | SealedSecret manifest completeness | G-18 | Executor score 70; `sealed-funnystuff.json` not visible in diff; scripts depend on it |
| P3 | Planner score consistency | G-05 | Planner score 70 but executor 98 — plan quality variance |

---

## 9. Artifact Reference

### Run directories

| Run ID | Cases | Path |
|--------|-------|------|
| `run_007` | G-01,03,05,06,08,10,14,17 | `evals/runs/run_007/` |
| `run_010` | G-02,04,07,09,11–13,15,16,18 | `evals/runs/run_010/` |
| `mixed_15_afterSemanticANDValidationChanges` | G-01–15 | `evals/runs/mixed_15_afterSemanticANDValidationChanges/` |
| `mixed_3_afterSemanticANDVlidationChanges` | G-15–18 | `evals/runs/mixed_3_afterSemanticANDVlidationChanges/` |
| `run_030` | G-01–18 | `evals/runs/run_030/` |

### Per-case artifact layout

```
evals/runs/<run_id>/
├── metadata.json                          # run config, experiment name, timestamp
├── artifacts/<case_id>.json               # full PipelineArtifact (all intermediate outputs)
└── judge_results/
    ├── planner/<case_id>_v1.json          # planner judge verdict + criteria scores
    └── executor/<case_id>_v1.json         # executor judge verdict + criteria scores
```

### Golden case sources

| File | Cases | Repo |
|------|-------|------|
| `evals/golden/unit_test/dvpwa.json` | G-01 – G-05 | Testing-Security-demo |
| `evals/golden/unit_test/vampi.json` | G-06 – G-09 | VAmPI |
| `evals/golden/unit_test/webgoat.json` | G-10 – G-13 | WebGoat |
| `evals/golden/unit_test/juiceshop.json` | G-14 – G-16 | Juice Shop |
| `evals/golden/unit_test/wrongsecrets.json` | G-17 – G-18 | WrongSecrets |

---

## 10. Methodology Notes

- **End-to-end pass** = executor judge `passed: true` AND `stage_failed` is `null` in the pipeline artifact.
- **Planner judge** is scored independently and reported but does not gate the e2e pass (see G-03 Pass 2: planner fail, executor pass).
- **Failure mode assignment** is based on artifact `stage_failed`, `error` field, `changed_files` vs `expected_files`, and executor/planner judge reasoning. When multiple modes apply, the earliest blocking stage wins.
- **Judge model:** `gpt-5.2-2025-12-11` (benchmark tier), version `v1`.
- **Baseline JSON:** `evals/baselines/current.json` — promoted from Pass 3 (`run_030`, 100% pass rate).

---