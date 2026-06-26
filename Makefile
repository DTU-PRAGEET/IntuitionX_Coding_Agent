.PHONY: cursor-poc orchestrator-dummy orchestrator-m13 orchestrator-m14 test

PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)

cursor-poc:
	$(PYTHON) -m src.integrations.cursor.poc --dry-run

orchestrator-dummy:
	$(PYTHON) -m src.core.orchestrator_loop

orchestrator-m13:
	$(PYTHON) -c "from src.core.orchestrator_loop import run_m13_chain; r=run_m13_chain(); print(r.success, r.fix_plan.get('primary_fix_type'))"

orchestrator-m14:
	$(PYTHON) -c "from src.core.orchestrator_loop import run_m14_chain; r=run_m14_chain(); print('success=', r.success); print('patch_status=', r.patch.get('status')); print('changed=', r.patch.get('changed_files'))"

test:
	$(PYTHON) -m pytest tests/ -q
