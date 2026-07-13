# Eval Results Quick Reference — `run_030` (Pass 3)

> **Eval index:** [evals/README.md](../README.md) · **Commands:** [eval_commands.md](eval_commands.md)

> **Run:** `evals/runs/run_030/`  
> **Date:** 2026-07-11  
> **Experiment:** `Skills-adjustment-Test3`  
> **Cases:** G-01 … G-18 (full 18-case security benchmark)

---

## Cross-pass summary

| Pass | Runs | Date | Experiment | Pass Rate | Planner mean | Executor mean |
|------|------|------|------------|-----------|--------------|---------------|
| **Pass 1 — Pre-validation** | `run_007` + `run_010` | 2026-07-04 | `mixed-10-unit` | **3 / 18 (16.7%)** | — | — |
| **Pass 2 — Post-validation** | `mixed_15` + `mixed_3` | 2026-07-06 | `mixed-15-unit-ChangingValidationLayerAndSemanticInstructions` | **13 / 18 (72.2%)** | 77.4 | 75.9 |
| **Pass 3 — Skills improvements** | `run_030` | 2026-07-11 | `Skills-adjustment-Test3` | **18 / 18 (100%)** | 94.9 | 96.8 |

**Net improvement (Pass 2 → Pass 3):** +27.8 pp · all 5 remaining failures recovered.

Compare runs locally:

```bash
python -m evals.runners.compare_runs run_026 run_030   # if run_026 report exists
python -m evals.runners.run_judge_eval --run-id run_030  # regenerate report.json
```

---

## Pass 3 per-case scorecard

| Case | Result | Planner | Executor | Pass 2 mode | Notes |
|------|--------|---------|----------|-------------|-------|
| G-01 | ✅ | 100 | 100 | — | Stable pass |
| G-02 | ✅ | 96 | 100 | — | Stable pass |
| G-03 | ✅ | 95 | 98 | — | Stable pass |
| G-04 | ✅ | 96 | 100 | `BROKEN_SYNTAX` | **Recovered** — config-driven debug/log_level |
| G-05 | ✅ | 70 | 98 | — | Stable pass |
| G-06 | ✅ | 100 | 98 | — | Stable pass |
| G-07 | ✅ | 98 | 100 | — | Stable pass |
| G-08 | ✅ | 98 | 100 | — | Stable pass |
| G-09 | ✅ | 100 | 100 | — | Stable pass |
| G-10 | ✅ | 95 | 96 | — | Stable pass |
| G-11 | ✅ | 95 | 95 | — | Stable pass |
| G-12 | ✅ | 97 | 98 | `PARTIAL_FIX` | **Recovered** — minimal IDOR guard, no restructure |
| G-13 | ✅ | 92 | 96 | — | Stable pass |
| G-14 | ✅ | 98 | 98 | `PARTIAL_FIX` | **Recovered** — component + template XSS co-fix |
| G-15 | ✅ | 90 | 96 | — | Stable pass |
| G-16 | ✅ | 100 | 100 | `PARTIAL_FIX` | **Recovered** — sync `jwt.verify` with RS256 pinning |
| G-17 | ✅ | 96 | 100 | — | Stable pass |
| G-18 | ✅ | 92 | 70 | `PARTIAL_FIX` | **Recovered** — plain Secret removed; soft failures on missing SealedSecret manifests |

---

## Pass 3 breakdown by dimension

| Dimension | Pass 2 | Pass 3 (run_030) | Δ |
|-----------|--------|------------------|---|
| **Overall** | 72.2% (13/18) | **100%** (18/18) | +27.8 pp |
| Python | 89% (8/9) | **100%** (9/9) | +11 pp |
| Java | 80% (4/5) | **100%** (5/5) | +20 pp |
| TypeScript | 33% (1/3) | **100%** (3/3) | +67 pp |
| YAML | 0% (0/1) | **100%** (1/1) | +100 pp |
| Misconfig | 0% (0/2) | **100%** (2/2) | +100 pp |
| XSS | 50% (1/2) | **100%** (2/2) | +50 pp |
| IDOR | 50% (1/2) | **100%** (2/2) | +50 pp |
| Auth | 75% (3/4) | **100%** (4/4) | +25 pp |

---

## Cases that flipped (Pass 2 → Pass 3)

### G-04 — Misconfig (python)

Pass 2 failed with `BROKEN_SYNTAX` (VERIFY target wrong file). Pass 3: planner added optional `debug`/`log_level` schema keys; executor wired `config['app'].get('debug', False)` in `sqli/app.py` and config-driven logging in `run.py`. Score: P96 / E100.

### G-12 — IDOR (java)

Pass 2 failed with `PARTIAL_FIX` (control-flow restructure). Pass 3: minimal ownership check `userId.equals(authUserId)` in `IDORViewOtherProfile.java`; hardcoded victim ID removed. Score: P97 / E98.

### G-14 — XSS (typescript)

Pass 2 failed with `PARTIAL_FIX` (`[innerHTML]` sink remained). Pass 3: removed `bypassSecurityTrustHtml` on search query **and** replaced `[innerHTML]="searchValue"` with `{{ searchValue }}` in template. Score: P98 / E98.

### G-16 — Auth (typescript)

Pass 2 failed with `PARTIAL_FIX` (async `jwt.verify` in sync function). Pass 3: replaced `jws.verify` with sync `jwt.verify(token, publicKey, { algorithms: ['RS256'] })` preserving boolean return. Score: P100 / E100.

### G-18 — Misconfig (yaml)

Pass 2 failed with `PARTIAL_FIX` (corrupted SealedSecret YAML). Pass 3: deleted plain `secrets-secret.yml` manifests; updated bootstrap scripts to use `sealed-funnystuff.json`. Executor judge passed (E70) with soft failures on missing SealedSecret manifest files in diff. Score: P92 / E70.

---

## Artifact paths

```
evals/runs/run_030/
├── metadata.json
├── report.json
├── artifacts/G-01.json … G-18.json
└── judge_results/
    ├── planner/G-01_v1.json … G-18_v1.json
    └── executor/G-01_v1.json … G-18_v1.json
```

**Baseline JSON:** `evals/baselines/current.json` — promoted from Pass 3 (`run_030`).
