# Evals (Phase 2)

Placeholder for the pytest-based eval harness and golden datasets.

Phase 2 milestones will add:

- `evals/golden/` — immutable security bug cases
- `evals/adversarial/` — edge-case scenarios
- `evals/baselines/current.json` — regression score tracking

Run evals (future):

```bash
make evals          # full suite
make evals-smoke    # PR gate subset
make evals-report   # HTML report
```
