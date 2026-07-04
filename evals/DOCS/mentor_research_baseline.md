# Research Agent — Baseline Run (2026-07-01)

## Executive summary

Live research eval ran on **18 golden security tickets** across 5 OWASP benchmark repos (DVPWA, VAmPI, WebGoat, Juice Shop, WrongSecrets).

| Metric | Result |
|--------|--------|
| Research completed | **17 / 18** (G-09 crashed on skill name) |
| Golden file found (recall) | **16 / 17** completed cases |
| File identification pass (`file_f1` ≥ 0.5) | **14 / 18** (**78%**) |
| Mean `file_f1` | **0.50** (milestone gate target: **0.85**) |

**Important:** The harness initially reported **0/18 pass** because it scored routing, plan, and implement stages that were never run in research-only mode. That is fixed; see `evals/reports/research_rescore.html` for the corrected scorecard.

---

## What the agent does well

- Locates the **correct vulnerable file** on most tickets (strong recall).
- Produces structured output: summary, ranked hypotheses, evidence, blast radius.
- Works across Python, Java, TypeScript, and YAML/K8s manifests.

### Example outputs (in `evals/DOCS/Research_results.json`)

| Ticket | Repo | Finding |
|--------|------|---------|
| G-01 | DVPWA | SQLi in `sqli/dao/student.py` — string-interpolated INSERT |
| G-10 | WebGoat | SQLi in `SqlInjectionLesson5a.java` — concatenated WHERE |
| G-18 | WrongSecrets | Committed K8s secret in `k8s/secrets-secret.yml` |

---

## Failures and gaps

| Ticket | Issue |
|--------|-------|
| G-09 | Agent called `load_skill('security/authentication')` — skill is `authentication-failures` (alias added) |
| G-02, G-16 | Found golden file but listed too many extras → `file_f1` below 0.5 |
| G-15 | Missed `routes/authenticatedUsers.ts` (only 50% recall on 2 expected files) |

### Infrastructure noise (not research quality)

- `cursor_search` uses **1 Cursor agent request per ticket** (~18 requests total).
- Temp log paths blocked by sandbox (`Path traversal blocked` on `/var/folders/.../tool_read_*.log`).
- `primitive_budget_exhausted` on several runs (8 read/bash cap).

---

## Per-case results (file_f1 only)

| Case | Pass | file_f1 | Notes |
|------|------|---------|-------|
| G-01 | ✓ | 0.67 | `sqli/dao/student.py` |
| G-02 | ✗ | 0.40 | + extra migration files |
| G-03 | ✓ | 0.80 | XSS: app.py + course template |
| G-04 | ✓ | 0.80 | debug in app.py + run.py |
| G-05 | ✓ | 0.50 | views.py + auth.py |
| G-06 | ✓ | 0.50 | users.py (IDOR password) |
| G-07 | ✓ | 0.50 | user_model.py SQLi |
| G-08 | ✓ | 0.50 | config.py weak JWT secret |
| G-09 | ✗ | 0.00 | skill load error |
| G-10 | ✓ | 0.50 | SqlInjectionLesson5a |
| G-11 | ✓ | 0.50 | JWTRefreshEndpoint |
| G-12 | ✓ | 0.50 | IDORViewOtherProfile |
| G-13 | ✓ | 0.50 | ProfileUpload path traversal |
| G-14 | ✓ | 0.50 | DOM XSS search-result component |
| G-15 | ✗ | 0.29 | missed authenticatedUsers.ts |
| G-16 | ✗ | 0.40 | + extra server/routes files |
| G-17 | ✓ | 0.67 | application.properties |
| G-18 | ✓ | 0.50 | secrets-secret.yml |

---
| What happened | Reality |
|-----------------------------------------|------|
| Research completed                      | 17/18 tickets (only G-09 crashed) |
| Found the correct golden file           | 16/17 completed cases |
| Real research pass rate (file_f1 ≥ 0.5) | 14/18 = 78% |
| Mean file identification score          | 50% (gate target is 85%) |

## Artifacts for review

- **Raw research JSON:** `evals/DOCS/Research_results.json`
- **Corrected scorecard:** `evals/reports/research_rescore.html`
- **Live run log:** terminal output from `make evals-research`

## Re-score without spending Cursor credits

```bash
make evals-rescore-research
```

## Next steps to hit 85% gate

1. List only the **primary fix file** in `affected_files`; put related files in hypotheses.
2. Re-run G-09 only (skill alias now maps `authentication` → `authentication-failures`).
3. Run **3–5 tickets** per live eval, not all 18 at once.
4. Consider grep-based search for research evals to avoid `cursor_search` cost.
