.PHONY: test evals evals-smoke evals-report evals-validate evals-sync \
	evals-routing evals-research evals-plan evals-implement evals-gate \
	evals-integration evals-integration-judge evals-sync-integration \
	evals-rescore-research evals-langsmith-routing evals-langsmith-research \
	evals-langsmith-plan evals-langsmith-implement evals-langsmith-integration \
	evals-langsmith-integration-judge

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

UNIT_CASES := evals/golden/unit_test
INTEGRATION_CASES := evals/golden/integration_test

test:
	$(PYTHON) -m pytest tests/ -q

# Validate every golden case against the schema (Milestone 2.2 gate).
evals-validate:
	$(PYTHON) -m evals.schema.case $(UNIT_CASES)
	$(PYTHON) -m evals.schema.case $(INTEGRATION_CASES)

# Offline smoke: score unit cases against recorded predictions (no LLM/Cursor).
evals-smoke: evals-validate
	$(PYTHON) -m evals.runners.run_eval --mode score \
		--cases $(UNIT_CASES) --predictions evals/fixtures/predictions.json \
		--out evals/reports/smoke --experiment week2-smoke

# Milestone 2.3 gate check on offline fixtures (research/plan/implement thresholds).
evals-gate: evals-smoke
	$(PYTHON) -m evals.runners.run_eval --mode score \
		--cases $(UNIT_CASES) --predictions evals/fixtures/predictions.json \
		--out evals/reports/gate --experiment week2-gate --gate --fixtures-only

# Sub-agent unit evals (live — require LLM; implement/e2e also need Cursor).
evals-routing:
	$(PYTHON) -m evals.runners.run_eval --mode routing --cases $(UNIT_CASES)

evals-research:
	$(PYTHON) -m evals.runners.run_eval --mode research --cases $(UNIT_CASES)

# Re-score saved research JSON (offline — no Cursor/LLM). Use after a live run.
evals-rescore-research:
	$(PYTHON) -m evals.runners.rescore_research_json \
		--input evals/DOCS/Research_results.json \
		--cases $(UNIT_CASES) \
		--out evals/reports/research_rescore

evals-plan:
	$(PYTHON) -m evals.runners.run_eval --mode plan --cases $(UNIT_CASES)

evals-implement:
	$(PYTHON) -m evals.runners.run_eval --mode implement --cases $(UNIT_CASES)

# Full live end-to-end suite on unit cases (requires LLM + Cursor credentials).
evals:
	$(PYTHON) -m evals.runners.run_eval --mode integration --cases $(UNIT_CASES) --no-dry-run

# Integration with artifact capture only (skip LLM judges during pipeline).
evals-integration-capture:
	$(PYTHON) -m evals.runners.run_eval --mode integration --cases $(UNIT_CASES) --no-dry-run --skip-judge

# Judge saved artifacts offline (no Cursor).
evals-integration-judge:
	@test -n "$(RUN_ID)" || (echo "Usage: make evals-integration-judge RUN_ID=run_001" && exit 1)
	$(PYTHON) -m evals.runners.run_judge_eval --run-id $(RUN_ID) --cases $(UNIT_CASES) --gate

# Integration tests — multi-repo, multi-issue tickets (e2e only).
evals-integration:
	$(PYTHON) -m evals.runners.run_eval --mode integration --cases $(INTEGRATION_CASES) --no-dry-run \
		--out evals/reports/integration

# Open the latest HTML scorecard.
evals-report:
	@open evals/reports/latest.html 2>/dev/null || echo "Report: evals/reports/latest.html"

# Mirror unit golden cases into a LangSmith dataset.
evals-sync:
	$(PYTHON) -m evals.sync_langsmith --dataset IntuitionX-Golden-20-v1 --cases $(UNIT_CASES)

evals-sync-integration:
	$(PYTHON) -m evals.sync_langsmith --dataset IntuitionX-Integration-5-v1 \
		--cases $(INTEGRATION_CASES) --description "IntuitionX integration set (5 single-repo multi-issue cases)"

# LangSmith experiments — unit golden set (require LANGCHAIN_API_KEY + make evals-sync).
evals-langsmith-routing:
	$(PYTHON) -m evals.runners.run_langsmith --mode routing \
		--dataset IntuitionX-Golden-20-v1 --experiment-prefix routing-baseline \
		--cases-dir $(UNIT_CASES)

evals-langsmith-research:
	$(PYTHON) -m evals.runners.run_langsmith --mode research \
		--dataset IntuitionX-Golden-20-v1 --experiment-prefix research-baseline \
		--cases-dir $(UNIT_CASES)

evals-langsmith-plan:
	$(PYTHON) -m evals.runners.run_langsmith --mode plan \
		--dataset IntuitionX-Golden-20-v1 --experiment-prefix plan-baseline \
		--cases-dir $(UNIT_CASES)

evals-langsmith-implement:
	$(PYTHON) -m evals.runners.run_langsmith --mode implement \
		--dataset IntuitionX-Golden-20-v1 --experiment-prefix implement-baseline \
		--cases-dir $(UNIT_CASES)

evals-langsmith-integration:
	$(PYTHON) -m evals.runners.run_langsmith --mode integration \
		--dataset IntuitionX-Integration-5-v1 --experiment-prefix integration-baseline \
		--cases-dir $(INTEGRATION_CASES)

evals-langsmith-integration-judge:
	$(PYTHON) -m evals.runners.run_langsmith --mode integration \
		--dataset IntuitionX-Golden-20-v1 --experiment-prefix integration-judge \
		--cases-dir $(UNIT_CASES)
