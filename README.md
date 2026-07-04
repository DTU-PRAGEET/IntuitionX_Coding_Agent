# Sprinklr Jira Solver

Autonomous coding platform for Sprinklr developers — Jira ticket to GitLab MR with LangGraph orchestration and Chainlit HITL.

## Quick Start

```bash
pip install -r requirements.txt
cp .env.production .env   # or create .env and add your keys

# Use Sprinklr IntuitionX LLM router (internal — no API key required)
LLM_PROVIDER=sprinklr
SPRINKLR_LLM_BASE_URL=http://qa6-intuitionx-llm-router-v2.sprinklr.com
SPRINKLR_LLM_MODEL=gpt-5.2-2025-12-11
SPRINKLR_LLM_CLIENT_IDENTIFIER=backend-platform-dev
SPRINKLR_LLM_PARTNER_ID=66006006
SPRINKLR_LLM_TRACKING_FEATURE=SPRINKLR_JIRA_SOLVER
# SPRINKLR_LLM_TRACKING_PARAMS={"feature":"CUSTOM_FEATURE"}  # optional JSON override

# API (CI/CD webhook)
uvicorn src.api.main:app --reload --port 8000

# Chainlit UI
chainlit run src/ui/chainlit_app.py --port 8080

# Docker
docker compose -f docker/docker-compose.yml up
```

## Architecture

- **Orchestrator** — Jira fetch, 4-route routing (FULL / SKIP_RESEARCH / MICRO / EXIT)
- **Research** — ReAct + signal-driven file discovery
- **Planner** — Execution contract with reflexion
- **Executor** — Git worktree + generic tools + GitLab MR
- **HITL** — Chainlit + LangGraph `interrupt`

## Architecture (Phase 1 — Pi-style)

- **Prompt-driven routing** in `src/agents/*/skills/scripts.md`
- **Thin Python runners** + `transform_context()` for token-minimal handoffs
- **Hardened tools** via `ToolRegistry` (`read`, `write`, `edit`, `bash`, `find`, `grep`)
- **LangGraph** for phase topology + HITL interrupts only
- **Chainlit** UI with tool event log + diff confirm before MR
- **API** `POST /approve/{thread_id}` for self-healing resume

See [AGENTS.md](AGENTS.md) and [docs/PRD_ADDENDUM_PHASE1.md](docs/PRD_ADDENDUM_PHASE1.md).
