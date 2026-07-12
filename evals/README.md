# Evals (Phase 2)

Pytest-based eval harness and golden datasets for the Sprinklr Jira Solver agent pipeline.

## Current baseline (Pass 3 — `run_030`)

| Metric | Value |
|--------|-------|
| **Pass rate** | **18 / 18 (100%)** |
| **Experiment** | `Skills-adjustment-Test3` |
| **Date** | 2026-07-11 |
| Planner judge mean | 94.9 |
| Executor judge mean | 96.8 |

Prior baselines: Pass 1 `run_007+run_010` (16.7%) · Pass 2 `mixed_15+mixed_3` (72.2%).

See `evals/baselines/current.json`, `evals/DOCS/failure-modes.md`, and `evals/DOCS/checking_evals_result.md` for full cross-pass analysis.

## Layout

- `evals/golden/` — immutable security bug cases
- `evals/adversarial/` — edge-case scenarios
- `evals/baselines/current.json` — regression score tracking
- `evals/runs/run_030/` — latest full 18-case integration run

Run evals:

```bash
make evals          # full suite
make evals-smoke    # PR gate subset
make evals-report   # HTML report

# Compare runs
python -m evals.runners.compare_runs run_026 run_030
```
