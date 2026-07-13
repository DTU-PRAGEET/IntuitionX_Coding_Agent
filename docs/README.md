# Documentation Index

> **Last updated:** 2026-07-13 — aligned with LangGraph pipeline, `skills/` plugin system, and Cursor SDK integration.

Use this index to find the right doc. **New developers:** start with [AGENT_PIPELINE_GUIDE.md](AGENT_PIPELINE_GUIDE.md), then per-directory READMEs under `src/*/README.md`.

---

## Architecture & pipeline

| Document | Purpose |
|----------|---------|
| [AGENT_PIPELINE_GUIDE.md](AGENT_PIPELINE_GUIDE.md) | Plain-language walkthrough: orchestrator → research → planner → executor |
| [CODEBASE_MAP.md](CODEBASE_MAP.md) | File-by-file classification; what runs in Chainlit vs tests |
| [project_structure.md](project_structure.md) | Function call trees per agent |
| [DATA_FLOW_AUDIT.md](DATA_FLOW_AUDIT.md) | `AgentState` field handoffs between nodes |

## Skills (Milestone 3.3)

| Document | Purpose |
|----------|---------|
| [**skills-authoring.md**](skills-authoring.md) | **Primary guide** — write, test, and publish skills without touching Python |
| [../skills/README.md](../skills/README.md) | Skill directory layout and reference skills |

## Evaluations

| Document | Purpose |
|----------|---------|
| [../evals/README.md](../evals/README.md) | Eval framework overview, artifacts, baselines |
| [../evals/DOCS/eval_commands.md](../evals/DOCS/eval_commands.md) | All `make evals-*` commands |
| [../evals/DOCS/evaluator_architecture.md](../evals/DOCS/evaluator_architecture.md) | How scoring and LLM judges work |
| [../evals/DOCS/failure-modes.md](../evals/DOCS/failure-modes.md) | Failure taxonomy + benchmark history |
| [../evals/DOCS/checking_evals_result.md](../evals/DOCS/checking_evals_result.md) | Quick scorecard for baseline `run_030` |

## Per-module READMEs (`src/`)

| Path | Content |
|------|---------|
| [../src/core/README.md](../src/core/README.md) | Shared kernel: state, graph, schemas, skill_manager |
| [../src/agents/README.md](../src/agents/README.md) | Four pipeline agents |
| [../src/api/README.md](../src/api/README.md) | REST API + HITL |
| [../src/integrations/README.md](../src/integrations/README.md) | Cursor SDK, Jira, LLM |
| [../src/pipeline/README.md](../src/pipeline/README.md) | Graph runner vs mock path |
| [../src/tools/README.md](../src/tools/README.md) | L1 primitives |
| [../src/ui/README.md](../src/ui/README.md) | Chainlit UI |
| [../src/memory/README.md](../src/memory/README.md) | Failed-attempt cache |

## API & operations

| Document | Purpose |
|----------|---------|
| [../src/api/API_GUIDE.md](../src/api/API_GUIDE.md) | REST API curl reference |
| [jira_webhook_setup.md](jira_webhook_setup.md) | Jira webhook → API |

## Testing repos (eval fixtures)

Docs under [Testing_repos/](Testing_repos/) and [Testing_repos_info/](Testing_repos_info/) describe **vulnerable benchmark repositories** used by `evals/golden/` — not runtime application code.

## Planning & history (reference only)

| Document | Note |
|----------|------|
| [Implementation_plan.md](Implementation_plan.md) | Historical implementation plan |
| [plan.md](plan.md) | Long-term architecture notes |
| [Project_state.md](Project_state.md) | Session handoff / milestone status |
| [Milestones.txt](Milestones.txt) | Roadmap checklist |
| [Solved_errors.md](Solved_errors.md) | Debug journal |

---

## Quick commands

```bash
chainlit run src/ui/chainlit_app.py --port 8080   # UI
uvicorn src.api.main:app --port 8000              # API
make evals-validate                               # golden schema validation (free)
make evals                                        # full 18-case integration benchmark
```
