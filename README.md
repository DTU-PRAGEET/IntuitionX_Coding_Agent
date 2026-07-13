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

- **Orchestrator** — Jira fetch, routing (`FULL` / `MICRO` / `EXIT`)
- **Research** — Deep agent loop + Cursor `cursor_search`
- **Planner** — Cursor Ask → `ExecutionPlan` JSON
- **Executor** — Git worktree + Cursor edits + LLM validation → GitHub PR
- **HITL** — Chainlit UI or REST API `POST /approve/{thread_id}`
- **Skills** — Markdown plugins in `skills/`; see [docs/skills-authoring.md](docs/skills-authoring.md)

## Architecture

- **Prompt-driven routing** in `skills/<agent>/<agent>.md` (loaded by `SkillRegistry`)
- **Thin Python runners** in `src/agents/*/agent.py`
- **Hardened tools** via `ToolRegistry` (`read`, `write`, `edit`, `bash`, `find`, `grep`, `ls`)
- **LangGraph** for phase topology + HITL interrupts (`src/core/graph.py`)
- **Chainlit** UI (`src/ui/chainlit_app.py`) or **FastAPI** (`src/api/main.py`)

See [docs/README.md](docs/README.md), [AGENTS.md](AGENTS.md), and [docs/AGENT_PIPELINE_GUIDE.md](docs/AGENT_PIPELINE_GUIDE.md).
