# Orchestrator skill

Routing skill for pipeline entry — decides **FULL / MICRO / EXIT**.

- **FULL** — Research → Gate 1 → Planner → Gate 2 → Executor
- **MICRO** — Planner → Gate 2 → Executor (skips Research; former `SKIP_RESEARCH` cases merge here)
- **EXIT** — no downstream agents

Explicit route requests in `user_prompt` take highest priority (except Step 2 EXIT hard stops).
