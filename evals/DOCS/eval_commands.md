# Eval Command Reference

Quick reference for running golden-case evals **per agent** and **full pipeline**, locally or on LangSmith.

Run all commands from the **repository root**.

---

## Prerequisites

```bash
# 1. Install deps
pip install -r requirements.txt

# 2. Configure .env (project root — gitignored)
#    LANGCHAIN_API_KEY=lsv2_...
#    LANGCHAIN_TRACING_V2=true
#    LLM_PROVIDER=...
#    CURSOR_API_KEY=...   # required for research / implement / e2e

# 3. LangSmith only — upload golden set once
make evals-sync

# 4. Free sanity check (no LLM, no Cursor)
make evals-validate
make evals-gate
```

---

## Golden case layout

```
evals/golden/
├── unit_test/           # 21 per-agent cases (G-00 + G-01–G-20)
│   ├── routing.json     # G-00, G-19, G-20
│   ├── dvpwa.json       # G-01 – G-05
│   ├── vampi.json       # G-06 – G-09
│   ├── webgoat.json     # G-10 – G-13
│   ├── juiceshop.json   # G-14 – G-16
│   └── wrongsecrets.json # G-17 – G-18
└── integration_test/    # 5 single-repo, multi-issue e2e cases (I-01 – I-05)
    └── integration.json
```

| Label | Case IDs | Used for |
|-------|----------|----------|
| **Unit tests** | G-00 + G-01–G-20 (**21** total) | Per-agent evals (routing → implement) |
| **Integration tests** | I-01–I-05 (**5** total) | Full pipeline e2e — one repo, multiple issues each |
| Security benchmark | G-01–G-18 (**18** cases) | Research, plan, implement unit evals |
| Routing-only EXIT | G-19, G-20 | Orchestrator EXIT tests only |
| Single smoke case | **G-01** (DVPWA SQLi) | Cheapest live research / pipeline smoke |

When docs say **“all 20”**, that usually means **G-01–G-20**. Research/plan/implement unit evals automatically skip G-19 and G-20 (routing-only).

---

## Two runners

| Runner | Output | Best for |
|--------|--------|----------|
| **`run_eval.py`** / `make evals-*` | Local JSON + HTML in `evals/reports/` | Gates, CI, quick pass/fail |
| **`run_langsmith.py`** / `make evals-langsmith-*` | LangSmith Experiments UI | Compare runs over time, traces + scores |

Both use the same agents and evaluators. LangSmith supports **`--case-ids`** for single-case runs; local `run_eval` uses **`--cases`** (path to a golden JSON file or directory).

**Default paths:** unit evals → `evals/golden/unit_test`; integration e2e → `evals/golden/integration_test`.

---

## 1. Orchestrator (routing only)

**Evaluates:** `route_correct`  
**Cursor cost:** 0  
**Cases:** all 21 (G-00 + G-01–G-20)

### LangSmith

```bash
# Single case
python -m evals.runners.run_langsmith \
  --mode routing \
  --case-ids G-01 \
  --experiment-prefix routing-G-01

# All 21 cases
make evals-langsmith-routing
# or
python -m evals.runners.run_langsmith \
  --mode routing \
  --experiment-prefix routing-baseline
```

### Local (HTML report)

```bash
# All 21 cases
make evals-routing
# or
python -m evals.runners.run_eval --mode routing \
  --cases evals/golden/unit_test \
  --out evals/reports/routing

# Routing EXIT smoke (3 cases: G-00, G-19, G-20)
python -m evals.runners.run_eval --mode routing \
  --cases evals/golden/unit_test/routing.json \
  --out evals/reports/routing_exit
```

For a **single local routing case**, use LangSmith with `--case-ids`, or point `--cases` at a one-case JSON (see [Single-case file helper](#single-case-file-helper) below).

---

## 2. Research agent

**Evaluates:** `file_f1`  
**Cursor cost:** ~1 request per case  
**Cases:** 18 (G-01–G-18)

### LangSmith

```bash
# Single case (~1 Cursor)
python -m evals.runners.run_langsmith \
  --mode research \
  --case-ids G-01 \
  --experiment-prefix research-G-01

# All 18 cases (~18 Cursor)
make evals-langsmith-research
# or
python -m evals.runners.run_langsmith \
  --mode research \
  --experiment-prefix research-baseline
```

### Local (HTML report)

```bash
# All 18 cases
make evals-research
# or
python -m evals.runners.run_eval --mode research \
  --cases evals/golden/unit_test \
  --out evals/reports/research

# Repo slice — DVPWA only (G-01–G-05, ~5 Cursor)
python -m evals.runners.run_eval --mode research \
  --cases evals/golden/unit_test/dvpwa.json \
  --out evals/reports/research_dvpwa \
  --export-predictions evals/fixtures/research_dvpwa.json

# Re-score saved outputs offline (free — no Cursor)
python -m evals.runners.run_eval --mode score \
  --predictions evals/fixtures/research_dvpwa.json \
  --fixtures-only --gate
```

---

## 3. Planner (routing + research + planner)

**Evaluates:** `fix_type_match`, `plan_line_overlap`  
**Cursor cost:** ~1 Cursor/case (research) + planner LLM  
**Cases:** 18 (G-01–G-18)

### LangSmith

```bash
# Single case
python -m evals.runners.run_langsmith \
  --mode plan \
  --case-ids G-01 \
  --experiment-prefix plan-G-01

# All 18 cases
make evals-langsmith-plan
# or
python -m evals.runners.run_langsmith \
  --mode plan \
  --experiment-prefix plan-baseline
```

### Local (HTML report)

```bash
# All 18 cases
make evals-plan
# or
python -m evals.runners.run_eval --mode plan \
  --cases evals/golden/unit_test \
  --out evals/reports/plan \
  --gate

# Repo slice (G-01–G-05)
python -m evals.runners.run_eval --mode plan \
  --cases evals/golden/unit_test/dvpwa.json \
  --out evals/reports/plan_dvpwa
```

---

## 4. Executor / implement (routing + research + planner + executor)

**Evaluates:** `patch_contains`  
**Default:** `dry_run=True` (no real patch apply)  
**Cursor cost:** research + planner + executor  
**Cases:** 18 (G-01–G-18)

### LangSmith

```bash
# Single case (dry_run patches)
python -m evals.runners.run_langsmith \
  --mode implement \
  --case-ids G-01 \
  --experiment-prefix implement-G-01

# All 18 cases
make evals-langsmith-implement
# or
python -m evals.runners.run_langsmith \
  --mode implement \
  --experiment-prefix implement-baseline
```

### Local (HTML report)

```bash
# All 18 cases (dry_run)
make evals-implement
# or
python -m evals.runners.run_eval --mode implement \
  --cases evals/golden/unit_test \
  --out evals/reports/implement

# Repo slice
python -m evals.runners.run_eval --mode implement \
  --cases evals/golden/unit_test/dvpwa.json \
  --out evals/reports/implement_dvpwa
```

---

## 5. Full pipeline (e2e)

**Runs:** routing → research → planner → executor  
**Evaluates (LangSmith e2e mode):** all five scorers  

### Unit e2e (21 cases)

```bash
make evals
# or
python -m evals.runners.run_eval --mode e2e \
  --cases evals/golden/unit_test \
  --no-dry-run \
  --out evals/reports/e2e
```

### Integration e2e (5 single-repo, multi-issue cases)

Each integration case bundles multiple security findings from one Tier-1 benchmark repo
(DVPWA, VAmPI, WebGoat, Juice Shop, WrongSecrets — see `docs/Testing_repos_info/`).

```bash
make evals-integration
# or
python -m evals.runners.run_eval --mode e2e \
  --cases evals/golden/integration_test \
  --no-dry-run \
  --out evals/reports/integration

# Single integration smoke
python -m evals.runners.run_langsmith \
  --mode e2e \
  --dataset IntuitionX-Integration-5-v1 \
  --case-ids I-01 \
  --cases-dir evals/golden/integration_test \
  --experiment-prefix integration-I-01
```

---

## Single-case file helper

`run_eval.py` filters by **file**, not `--case-ids`. To run one case locally, build a one-case golden file:

```bash
CASE=G-01
python - <<'PY'
import json, os, sys
from pathlib import Path
case_id = os.environ["CASE"]
for f in sorted(Path("evals/golden/unit_test").glob("*.json")):
    for c in json.loads(f.read_text()):
        if c["case_id"] == case_id:
            out = Path(f"evals/fixtures/_single_{case_id}.json")
            out.write_text(json.dumps([c], indent=2) + "\n")
            print(f"wrote {out}")
            sys.exit(0)
sys.exit(f"case {case_id} not found")
PY

python -m evals.runners.run_eval --mode research \
  --cases evals/fixtures/_single_G-01.json \
  --out evals/reports/research_G-01
```

Replace `--mode research` with `plan`, `implement`, or `e2e` as needed.

---

## Save live outputs for free re-score

After any live `run_eval` batch, export predictions and re-score without spending Cursor:

```bash
python -m evals.runners.run_eval --mode research \
  --cases evals/golden/unit_test/dvpwa.json \
  --export-predictions evals/fixtures/my_research_run.json \
  --out evals/reports/research_live

python -m evals.runners.run_eval --mode score \
  --predictions evals/fixtures/my_research_run.json \
  --fixtures-only \
  --gate \
  --out evals/reports/research_rescore
```

---

## Cheat sheet

| Goal | Command |
|------|---------|
| Free validate | `make evals-validate && make evals-gate` |
| Sync to LangSmith | `make evals-sync` |
| Routing — 1 case | `run_langsmith --mode routing --case-ids G-01` |
| Routing — all 21 | `make evals-langsmith-routing` |
| Research — 1 case | `run_langsmith --mode research --case-ids G-01` |
| Research — all 18 | `make evals-langsmith-research` |
| Plan — 1 case | `run_langsmith --mode plan --case-ids G-01` |
| Plan — all 18 | `make evals-langsmith-plan` |
| Implement — 1 case | `run_langsmith --mode implement --case-ids G-01` |
| Implement — all 18 | `make evals-langsmith-implement` |
| E2E — 1 case | `run_langsmith --mode e2e --case-ids G-01` |
| E2E — all 18 | `run_langsmith --mode e2e --experiment-prefix e2e-baseline` |
| E2E — all (local, unit) | `make evals` |
| Integration e2e — all 5 | `make evals-integration` |
| Sync integration to LangSmith | `make evals-sync-integration` |
| Open last HTML report | `make evals-report` |

---

## Cursor budget guide

| Run | Approx. Cursor requests |
|-----|-------------------------|
| `routing` (any count) | 0 |
| `research --case-ids G-01` | ~1 |
| `research` all 18 | ~18 |
| `plan` all 18 | ~18 (research) + planner LLM |
| `implement` / `e2e` all 18 | ~18+ (research + executor) |

**Recommended order:** validate (free) → routing (free) → research G-01 smoke → expand to repo slice → full baseline.

---

## Golden files by repo (unit_test/)

| File | Cases |
|------|-------|
| `evals/golden/unit_test/routing.json` | G-00, G-19, G-20 |
| `evals/golden/unit_test/dvpwa.json` | G-01 – G-05 |
| `evals/golden/unit_test/vampi.json` | G-06 – G-09 |
| `evals/golden/unit_test/webgoat.json` | G-10 – G-13 |
| `evals/golden/unit_test/juiceshop.json` | G-14 – G-16 |
| `evals/golden/unit_test/wrongsecrets.json` | G-17 – G-18 |

| Integration file | Cases |
|------------------|-------|
| `evals/golden/integration_test/integration.json` | I-01 – I-05 |

Use `--cases evals/golden/unit_test/dvpwa.json` for repo-level unit runs.
Use `--cases evals/golden/integration_test` for single-repo multi-issue e2e runs.
