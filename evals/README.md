# `evals/` — Agent Evaluation Framework

## Purpose

This directory is the **benchmark harness** for the Jira Solver pipeline. It runs golden test cases against real vulnerable repos (DVPWA, VAmPI, WebGoat, Juice Shop, WrongSecrets), captures full pipeline artifacts, scores outputs with deterministic checks and LLM judges, and tracks baselines over time.

Use evals to:
- Measure regression after skill or code changes
- Debug why a case failed (research, planner, or executor)
- Compare pass rates across benchmark passes

## Quick start

```bash
# Free validation (no LLM, no Cursor)
make evals-validate && make evals-gate

# Full 18-case integration benchmark (uses Cursor + LLM judges)
make evals

# Re-score a saved run without re-running agents
python -m evals.runners.run_judge_eval --run-id run_030
```

Full command reference: [`DOCS/eval_commands.md`](DOCS/eval_commands.md)

---

## Directory layout

```
evals/
├── DOCS/                    # ← Important documentation (read these)
│   ├── failure-modes.md     # Failure taxonomy + benchmark history
│   ├── checking_evals_result.md  # Quick scorecard for latest baseline
│   ├── eval_commands.md     # All make/python eval commands
│   └── evaluator_architecture.md # How scoring works end-to-end
├── golden/                  # Immutable answer keys (test cases)
│   ├── unit_test/           # G-00 … G-20 per-agent cases
│   └── integration_test/    # I-01 … I-05 multi-issue e2e cases
├── runs/                    # Saved pipeline runs (artifacts + judge verdicts)
│   └── run_XXX/
├── baselines/               # Promoted benchmark snapshots
│   └── current.json         # ← Current baseline (Pass 3, 100% on G-01–G-18)
├── evaluators/              # Scoring functions (file_f1, planner_judge, etc.)
├── harness/                 # Live pipeline targets → Prediction
├── runners/                 # CLI entry points (run_eval, run_judge_eval, …)
├── artifacts/               # PipelineArtifact schema
├── prompts/                 # LLM judge system prompts
├── reports/                 # HTML/JSON scorecards from local runs
├── fixtures/                # Saved predictions for offline re-score
├── gates.py                 # Milestone gate thresholds
├── report.py                # Scorecard aggregation
└── langsmith_adapter.py     # LangSmith experiment wrappers
```

---

## Important files explained

### `DOCS/failure-modes.md` — Failure taxonomy

**Read this when a case fails.** Classifies every failure into one of five modes:

| Mode | Code | Meaning |
|------|------|---------|
| Wrong File | `WRONG_FILE` | Edited wrong file — none of golden `expected_files` touched |
| Wrong Fix Type | `WRONG_FIX_TYPE` | Right file, wrong remediation approach |
| Broken Syntax | `BROKEN_SYNTAX` | Incomplete or syntactically invalid code |
| Partial Fix | `PARTIAL_FIX` | Patch applied but vulnerability not fully closed |
| No Output | `NO_OUTPUT` | Pipeline aborted before judgeable patch |

Also documents **three benchmark passes** on G-01–G-18:
- Pass 1 (16.7%) — pre-validation baseline
- Pass 2 (72.2%) — post semantic-targeting + validation layer
- Pass 3 (100%) — post skills improvements (`run_030`)

### `baselines/current.json` — Current baseline

**Read this for the promoted benchmark snapshot.** Contains:
- `run_id` — source run (`run_030`)
- `pass_rate` — overall pass rate (1.0 = 100%)
- `evaluator_means` — planner_judge, executor_judge averages
- `scorecard` — breakdown by vuln_class, language, difficulty

Use to compare a new run against the known-good baseline.

### `DOCS/checking_evals_result.md` — Quick results reference

Per-case scorecard for `run_030` (Pass 3): planner/executor scores, which cases recovered from Pass 2, artifact paths.

### `DOCS/evaluator_architecture.md` — How scoring works

Deep dive: golden case schema, each evaluator metric, LLM judge criteria, how to map failures to taxonomy modes. **Start here if you are new to evals.**

### `DOCS/eval_commands.md` — Command cheat sheet

Every `make evals-*` and `python -m evals.runners.*` command with Cursor budget estimates.

---

## Golden cases (`golden/`)

### Unit tests (`golden/unit_test/`)

| File | Cases | Repo |
|------|-------|------|
| `routing.json` | G-00, G-19, G-20 | Routing-only (EXIT tests) |
| `dvpwa.json` | G-01 – G-05 | DVPWA (Python) |
| `vampi.json` | G-06 – G-09 | VAmPI (Python) |
| `webgoat.json` | G-10 – G-13 | WebGoat (Java) |
| `juiceshop.json` | G-14 – G-16 | Juice Shop (TypeScript) |
| `wrongsecrets.json` | G-17 – G-18 | WrongSecrets (Java/YAML) |

**Security benchmark:** G-01–G-18 (18 cases). Routing-only G-19, G-20 are excluded from research/plan/implement evals.

Each case defines:
- `ticket` — synthetic Jira input
- `expected_files`, `expected_fix_type`, `expected_patch_contains` — answer key
- `eval_scope` — which stages to score (`routing`, `research`, `plan`, `implement`)
- `repo.commit_sha` — pinned commit for reproducibility

### Integration tests (`golden/integration_test/`)

| File | Cases |
|------|-------|
| `integration.json` | I-01 – I-05 |

Multi-issue e2e cases — one repo, multiple vulnerabilities per run.

---

## Run artifacts (`runs/run_XXX/`)

After an integration eval, each run directory contains:

```
evals/runs/run_030/
├── metadata.json              # Run config, experiment name, timestamp
├── report.json                # Aggregated scorecard (regenerate with run_judge_eval)
├── artifacts/
│   ├── G-01.json              # Full PipelineArtifact per case
│   └── G-02.json … G-18.json
└── judge_results/
    ├── planner/
    │   └── G-01_v1.json       # Planner LLM judge verdict + criteria scores
    └── executor/
        └── G-01_v1.json       # Executor LLM judge verdict + criteria scores
```

### How to read `artifacts/{case_id}.json`

Frozen snapshot of the entire pipeline for one case:

| Section | Content |
|---------|---------|
| `research_output` | Summary, hypotheses, affected_files |
| `plan` | Full ExecutionPlan JSON |
| `plan_validation` | Deterministic validation results |
| `git_diff` | Actual patch applied |
| `before_context` / `after_context` | ±50 lines around each EDIT target |
| `step_results` | READ/EDIT/VERIFY pass/fail per step |
| `stage_failed` | `null` if completed; else `research`/`planner`/`executor` |
| `golden` | Copy of answer-key fields for judge context |

**End-to-end pass criterion:** executor judge `passed: true` AND `stage_failed` is `null`.

### How to read `judge_results/*/case_v1.json`

LLM judge verdict with weighted criteria:

```json
{
  "score": 96,
  "passed": true,
  "criteria": {
    "security_fix_quality": {"score": 98, "reason": "..."},
    "code_correctness": {"score": 100, "reason": "..."}
  },
  "hard_failures": [],
  "soft_failures": [],
  "reasoning": "..."
}
```

**Pass threshold:** normalized score ≥ 0.75 per judge. Case passes only if **all** applicable evaluators pass.

### How to read `metadata.json`

```json
{
  "run_id": "run_030",
  "experiment": "Skills-adjustment-Test3",
  "timestamp": "...",
  "cases": ["G-01", "G-02", ...],
  "dry_run": false
}
```

Use `experiment` to label what changed between runs.

---

## Evaluators (what gets scored)

| Key | Scope | Threshold | What it measures |
|-----|-------|-----------|------------------|
| `route_correct` | routing | 1.0 | Orchestrator picked correct route |
| `file_f1` | research | 0.5 | Research found expected files (F1 on path tails) |
| `planner_preflight` | plan | 0.90 | Deterministic plan structure checks |
| `planner_judge` | plan | 0.75 | LLM review of execution plan quality |
| `executor_preflight` | implement | 0.90 | Executor structural checks before judge |
| `executor_judge` | implement | 0.75 | LLM review of applied patch |

Registry: `evaluators/__init__.py` → `EVALUATORS`

**Debugging a failure — decision flow:**

1. Check `stage_failed` in artifact → `NO_OUTPUT` if set
2. Check `file_f1` → `WRONG_FILE` if below threshold
3. Check `planner_judge` criteria → `WRONG_FIX_TYPE` if plan wrong
4. Check `executor_judge` criteria → `BROKEN_SYNTAX` / `PARTIAL_FIX` if patch wrong

Full flowchart: [`DOCS/evaluator_architecture.md`](DOCS/evaluator_architecture.md) §8

---

## Runners

| Script | Purpose |
|--------|---------|
| `runners/run_eval.py` | Main local runner — predict + score + HTML report |
| `runners/run_judge_eval.py` | Score saved artifacts offline (no Cursor cost) |
| `runners/run_langsmith.py` | Same agents/evaluators, LangSmith UI traces |
| `runners/compare_runs.py` | Diff two run scorecards |

---

## Comparing runs

```bash
# Regenerate report from saved artifacts
python -m evals.runners.run_judge_eval --run-id run_030

# Compare two runs
python -m evals.runners.compare_runs run_026 run_030
```

---

## Related documentation

| Doc | Content |
|-----|---------|
| [`DOCS/failure-modes.md`](DOCS/failure-modes.md) | Failure taxonomy + Pass 1/2/3 history |
| [`DOCS/checking_evals_result.md`](DOCS/checking_evals_result.md) | Pass 3 per-case scorecard |
| [`DOCS/eval_commands.md`](DOCS/eval_commands.md) | All eval commands |
| [`DOCS/evaluator_architecture.md`](DOCS/evaluator_architecture.md) | Scoring deep dive |
| [`docs/Testing_repos_info/`](../docs/Testing_repos_info/) | Vulnerable repo descriptions |
| [`docs/eval_routing_labels.md`](../docs/eval_routing_labels.md) | Routing eval label reference |
| [`skills/README.md`](../skills/README.md) | Skills that evals measure improvements against |
