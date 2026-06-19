# Sprinklr Jira Solver — Production-Grade Development Plan
### Lead Architect Reference Document | Pi-Style Iterative Build

---

## Guiding Principles Before We Start

| Principle | What It Means In Practice |
|---|---|
| **Skeleton-First** | Wire the full graph with stubs in M1. Every milestone fleshes out one node. |
| **Skills-as-Config** | Agent intelligence lives in `skills/scripts.md`. Python is just the runner. |
| **One-Env-Variable Swap** | `LLM_PROVIDER=local` → `LLM_PROVIDER=anthropic`. Zero agent code changes. |
| **Trace Everything** | LangSmith runs from M1. Every stub emits a trace. Mentor sees the whole graph live. |
| **Schema-Driven** | The `AgentState` TypedDict is the contract. Agents read from it, write to it, nothing else. |

---

## Project Directory Structure (Finalized in M1)

```
sprinklr-jira-solver/
│
├── src/
│   ├── core/
│   │   ├── state.py          # Global AgentState schema — single source of truth
│   │   ├── llm.py            # LLM abstraction layer — the one-swap design
│   │   ├── graph.py          # LangGraph graph wiring (all nodes registered here)
│   │   └── config.py         # Env-based config loader
│   │
│   ├── agents/
│   │   ├── orchestrator/
│   │   │   ├── agent.py
│   │   │   └── skills/
│   │   │       └── scripts.md   # Orchestrator's prompt brain
│   │   ├── research/
│   │   │   ├── agent.py
│   │   │   └── skills/
│   │   │       └── scripts.md
│   │   ├── planner/
│   │   │   ├── agent.py
│   │   │   └── skills/
│   │   │       └── scripts.md
│   │   └── executor/
│   │       ├── agent.py
│   │       └── skills/
│   │           └── scripts.md
│   │
│   ├── tools/
│   │   ├── jira_tool.py
│   │   ├── bash_tool.py
│   │   ├── ast_tool.py
│   │   ├── web_search_tool.py
│   │   ├── git_worktree_tool.py
│   │   └── build_config_tool.py
│   │
│   └── memory/
│       └── cache.py          # Redis-backed failed-attempt store
│
├── docker/
│   ├── Dockerfile
│   └── docker-compose.yml    # Local dev: app + Ollama + Redis + MongoDB
│
├── k8s/
│   ├── configmap.yaml
│   ├── deployment.yaml
│   ├── service.yaml
│   └── secrets.yaml
│
├── .env.local                # LLM_PROVIDER=local, OLLAMA_BASE_URL=...
├── .env.production           # LLM_PROVIDER=anthropic, ANTHROPIC_API_KEY=...
└── requirements.txt
```

---

## The Master State Schema (`src/core/state.py`)
> Defined once in M1. Every agent reads and writes ONLY to this. Never pass raw dicts between agents.

```python
from typing import TypedDict, Optional, List, Literal, Annotated
from langgraph.graph.message import add_messages

# ── Sub-schemas ─────────────────────────────────────────────────────────────

class JiraTicket(TypedDict):
    id: str
    status: str                        # DONE check happens here
    title: str
    description: str
    priority: Literal["P0","P1","P2","P3"]
    labels: List[str]
    reporter: str

class ResearchOutput(TypedDict):
    root_cause_hypotheses: List[str]   # Ordered by confidence
    affected_files: List[str]          # Absolute repo paths
    priority_order: List[str]          # Which files to touch first
    architectural_risk: Literal["LOW", "MEDIUM", "HIGH"]
    dependency_map: dict               # file → List[dependent_files]
    external_references: List[str]     # URLs from web search

class TodoItem(TypedDict):
    step_id: str                       # e.g. "step_001"
    file_path: str                     # Absolute path
    action: Literal["CREATE", "MODIFY", "DELETE"]
    description: str                   # Human-readable intent
    command_sequence: List[str]        # Exact shell commands in order
    estimated_complexity: Literal["LOW", "MEDIUM", "HIGH"]

class PlanOutput(TypedDict):
    todo_list: List[TodoItem]
    file_edit_strategy: str            # Narrative explanation
    total_estimated_risk: Literal["LOW", "MEDIUM", "HIGH"]
    rollback_strategy: str             # What to do if executor fails

class ExecutionOutput(TypedDict):
    mr_url: str
    changed_files: List[str]
    git_diff_summary: str
    worktree_path: str
    compilation_status: Literal["PASS", "FAIL"]

class HumanApprovalGate(TypedDict):
    gate_id: str
    phase: Literal["RESEARCH", "PLANNING"]
    status: Literal["PENDING", "APPROVED", "REJECTED"]
    feedback: Optional[str]            # Populated on REJECTED
    timestamp: str

class FailedAttempt(TypedDict):
    attempt_id: str
    ticket_id: str
    phase_failed: str
    error_summary: str
    plan_used: Optional[PlanOutput]
    timestamp: str

# ── Master State ─────────────────────────────────────────────────────────────

class AgentState(TypedDict):
    # ── Inputs ────────────────────────────────────────
    jira_ticket_id: str
    user_prompt: str
    context_image: Optional[str]           # base64 or file path

    # ── Jira Resolved Data ─────────────────────────────
    jira_ticket: Optional[JiraTicket]

    # ── Orchestrator Decisions ─────────────────────────
    routing_decision: Optional[Literal[
        "DIRECT_IMPLEMENTATION",           # Skip Research+Planning
        "FULL_PIPELINE"                    # Full 4-phase flow
    ]]
    current_phase: Literal[
        "INIT", "ORCHESTRATOR",
        "RESEARCH", "PLANNING",
        "IMPLEMENTATION", "REVIEW", "DONE", "EXIT"
    ]
    risk_level: Optional[Literal["LOW", "MEDIUM", "HIGH"]]

    # ── Agent Outputs ──────────────────────────────────
    research_output: Optional[ResearchOutput]
    plan_output: Optional[PlanOutput]
    execution_output: Optional[ExecutionOutput]

    # ── Human Approval Gates ───────────────────────────
    human_gates: List[HumanApprovalGate]

    # ── Self-Healing / Memory ──────────────────────────
    previous_attempts: List[FailedAttempt] # Fetched from Redis at start
    current_error_log: Optional[str]        # CI/CD error fed back here
    retry_count: int                        # Global retry counter
    max_retries: int                        # Config-driven ceiling

    # ── Token Budget ───────────────────────────────────
    total_tokens_used: int
    token_budget: int                       # Hard ceiling from config

    # ── Message History (LangGraph native) ─────────────
    messages: Annotated[list, add_messages]

    # ── Session Metadata ───────────────────────────────
    session_id: str
    created_at: str
    updated_at: str
```

---

## The LLM Abstraction Layer (`src/core/llm.py`)
> The one-swap design. `LLM_PROVIDER` in `.env` is the only variable that changes.

```python
import os
from enum import Enum
from langchain_anthropic import ChatAnthropic
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI
from langchain_core.language_models import BaseChatModel

class LLMProvider(str, Enum):
    LOCAL  = "local"       # Ollama during dev
    ANTHROPIC = "anthropic"
    OPENAI = "openai"

class TaskComplexity(str, Enum):
    LOW    = "low"     # Orchestrator routing decisions, status checks
    MEDIUM = "medium"  # Research, Planning
    HIGH   = "high"    # Complex reasoning, self-healing synthesis

# Model matrix — swap this table when adding new providers
_MODEL_MATRIX = {
    LLMProvider.LOCAL: {
        TaskComplexity.LOW:    "codellama:7b",
        TaskComplexity.MEDIUM: "codellama:13b",
        TaskComplexity.HIGH:   "codellama:34b",
    },
    LLMProvider.ANTHROPIC: {
        TaskComplexity.LOW:    "claude-haiku-4-5-20251001",
        TaskComplexity.MEDIUM: "claude-sonnet-4-6",
        TaskComplexity.HIGH:   "claude-opus-4-6",
    },
    LLMProvider.OPENAI: {
        TaskComplexity.LOW:    "gpt-4o-mini",
        TaskComplexity.MEDIUM: "gpt-4o",
        TaskComplexity.HIGH:   "gpt-4o",
    },
}

def get_llm(complexity: TaskComplexity = TaskComplexity.MEDIUM) -> BaseChatModel:
    provider = LLMProvider(os.getenv("LLM_PROVIDER", "local"))
    model_name = _MODEL_MATRIX[provider][complexity]

    if provider == LLMProvider.LOCAL:
        return ChatOllama(
            model=model_name,
            base_url=os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
        )
    elif provider == LLMProvider.ANTHROPIC:
        return ChatAnthropic(
            model=model_name,
            api_key=os.getenv("ANTHROPIC_API_KEY")
        )
    elif provider == LLMProvider.OPENAI:
        return ChatOpenAI(
            model=model_name,
            api_key=os.getenv("OPENAI_API_KEY")
        )
```

**Routing Rule:** Each agent calls `get_llm()` with the appropriate complexity:
- Orchestrator status check → `LOW`
- Orchestrator routing decision → `MEDIUM`
- Research Agent tool synthesis → `HIGH`
- Planner Agent TODO generation → `HIGH`
- Executor Agent (dumb, instruction-following) → `MEDIUM`

---

## The `skills/scripts.md` Template (Universal Pattern)

Every agent's `skills/scripts.md` follows this exact structure.
The agent's `agent.py` reads this file at startup and injects it as the system prompt.

```markdown
# [AGENT_NAME] — Skills & Execution Scripts

## PERSONA
You are [precise role description]. You are NOT responsible for [anti-pattern].
You receive a structured context object and produce a structured output. Nothing else.

## REASONING STRATEGY (MANDATORY — follow exactly)
For every task, execute this loop:
1. **THINK**: Restate what you know from the input context.
2. **IDENTIFY**: What is the specific sub-problem you need to solve right now?
3. **SELECT TOOL**: Which tool solves this sub-problem? Why?
4. **ACT**: Call the tool with precise parameters.
5. **OBSERVE**: What did the tool return? Is it sufficient?
6. **REFLECT**: Does this change your hypothesis? Do you need another tool?
7. **OUTPUT**: Only when confident, produce the final structured JSON output.

## AVAILABLE TOOLS
### tool_name
- **Purpose**: [one-line purpose]
- **When to use**: [specific trigger condition]
- **Input**: `{ param: type }`
- **Output**: `{ result: type }`

## INPUT CONTRACT
You will receive: [list of state fields this agent reads]

## OUTPUT CONTRACT
Return ONLY a valid JSON object. No preamble. No explanation outside the JSON.
Schema:
{
  "field_1": "type and description",
  "field_2": "type and description"
}

## SELF-HEALING INSTRUCTIONS
If you detect a previous failed attempt in `previous_attempts`:
1. Read the `error_summary` of the last failure.
2. Explicitly avoid the same approach.
3. Note your deviation in the `file_edit_strategy` field.

## HARD CONSTRAINTS
- Never hallucinate file paths. Only use paths confirmed by tools.
- Never exceed [X] tool calls per reasoning loop.
- If token budget is below [Y], produce a minimal viable output immediately.
```

---

---

# MILESTONE 1 — Backend Foundation
**Goal:** Stable, traceable environment before any agent logic. Mentor demo: show the LangGraph graph topology with all stubs wired and visible in LangSmith.

## Components Built
1. Full project directory scaffold
2. `AgentState` TypedDict (complete, as defined above)
3. LLM abstraction layer (complete)
4. LangGraph graph with all 4 nodes registered as **stubs**
5. LangSmith project initialized
6. Docker Compose for local dev
7. K8s ConfigMap and Deployment stubs

## LangGraph Graph Stub (`src/core/graph.py`)

```python
from langgraph.graph import StateGraph, END
from src.core.state import AgentState

# Import stubs — each returns state unchanged in M1
from src.agents.orchestrator.agent import orchestrator_node
from src.agents.research.agent    import research_node
from src.agents.planner.agent     import planner_node
from src.agents.executor.agent    import executor_node

def build_graph() -> StateGraph:
    graph = StateGraph(AgentState)

    # Register all nodes
    graph.add_node("orchestrator", orchestrator_node)
    graph.add_node("research",     research_node)
    graph.add_node("planner",      planner_node)
    graph.add_node("executor",     executor_node)

    # Entry point
    graph.set_entry_point("orchestrator")

    # Conditional routing from Orchestrator (stub returns "research" always in M1)
    graph.add_conditional_edges(
        "orchestrator",
        lambda state: state.get("routing_decision", "FULL_PIPELINE"),
        {
            "FULL_PIPELINE":            "research",
            "DIRECT_IMPLEMENTATION":    "executor",
            "EXIT":                     END,
        }
    )

    graph.add_edge("research",  "planner")
    graph.add_edge("planner",   "executor")
    graph.add_edge("executor",  END)

    return graph.compile()
```

**Stub example (all agents look like this in M1):**
```python
# src/agents/research/agent.py  (M1 stub)
from src.core.state import AgentState

def research_node(state: AgentState) -> AgentState:
    print("[STUB] Research Agent — not implemented yet")
    return {**state, "current_phase": "RESEARCH"}
```

## LangSmith Setup

**In `.env.local`:**
```
LANGCHAIN_TRACING_V2=true
LANGCHAIN_API_KEY=your_langsmith_key
LANGCHAIN_PROJECT=sprinklr-jira-solver-dev
LANGCHAIN_ENDPOINT=https://api.smith.langchain.com
```

**In `.env.production`:**
```
LANGCHAIN_PROJECT=sprinklr-jira-solver-prod
```

No code changes needed. LangChain/LangGraph auto-emits traces when these vars are set. Every node invocation appears as a named span in LangSmith — even stubs. **This is your mentor's live dashboard from day one.**

For custom span naming, add this decorator to any node function:
```python
from langsmith import traceable

@traceable(name="Orchestrator — Route Decision")
def orchestrator_node(state: AgentState) -> AgentState:
    ...
```

## Docker Compose (`docker/docker-compose.yml`)

```yaml
version: "3.9"
services:
  app:
    build: .
    env_file: .env.local
    ports:
      - "8000:8000"
    volumes:
      - ./src:/app/src
      - ./skills:/app/skills
    depends_on:
      - ollama
      - redis
      - mongo

  ollama:                           # Local LLM server
    image: ollama/ollama
    ports:
      - "11434:11434"
    volumes:
      - ollama_data:/root/.ollama

  redis:                            # Cache Memory for failed attempts
    image: redis:7-alpine
    ports:
      - "6379:6379"

  mongo:                            # Persistent session storage
    image: mongo:7
    ports:
      - "27017:27017"
    volumes:
      - mongo_data:/data/db

volumes:
  ollama_data:
  mongo_data:
```

## K8s Stubs (`k8s/configmap.yaml`) — Introduced in M1, populated in M6

```yaml
apiVersion: v1
kind: ConfigMap
metadata:
  name: jira-solver-config
data:
  LLM_PROVIDER: "anthropic"
  LANGCHAIN_PROJECT: "sprinklr-jira-solver-prod"
  REDIS_URL: "redis://redis-service:6379"
  MONGO_URL: "mongodb://mongo-service:27017"
```

## `skills/` File Initialized This Milestone
- `src/agents/orchestrator/skills/scripts.md` — stub template with persona only
- `src/agents/research/skills/scripts.md` — stub template
- `src/agents/planner/skills/scripts.md` — stub template
- `src/agents/executor/skills/scripts.md` — stub template

## Mentor Showcase
Run `docker compose up`, trigger a test Jira ID, open LangSmith → see all 4 nodes fire in sequence with their stub logs, full graph topology visible.

---

# MILESTONE 2 — Orchestrator Agent
**Goal:** The real brain is live. It fetches the Jira ticket, makes the DONE check, decides routing, and manages global state. All other agents remain stubs.

## Components Built
1. Jira Tool
2. Orchestrator Agent (full logic)
3. LLM routing decision (complexity-based)
4. Token tracker initialization
5. `orchestrator/skills/scripts.md` fully written

## Jira Tool (`src/tools/jira_tool.py`)

```python
from jira import JIRA
from src.core.state import JiraTicket
import os

def fetch_jira_ticket(ticket_id: str) -> JiraTicket:
    jira = JIRA(
        server=os.getenv("JIRA_SERVER_URL"),
        basic_auth=(os.getenv("JIRA_EMAIL"), os.getenv("JIRA_API_TOKEN"))
    )
    issue = jira.issue(ticket_id)
    return JiraTicket(
        id=issue.key,
        status=issue.fields.status.name,
        title=issue.fields.summary,
        description=issue.fields.description or "",
        priority=issue.fields.priority.name,
        labels=issue.fields.labels,
        reporter=issue.fields.reporter.displayName,
    )
```

## Orchestrator Agent (`src/agents/orchestrator/agent.py`)

```python
from langsmith import traceable
from src.core.state import AgentState
from src.core.llm import get_llm, TaskComplexity
from src.tools.jira_tool import fetch_jira_ticket
import uuid, json
from datetime import datetime, timezone
from pathlib import Path

SKILLS_PROMPT = Path(__file__).parent / "skills/scripts.md"

@traceable(name="Orchestrator — Main")
def orchestrator_node(state: AgentState) -> AgentState:
    # 1. Fetch Jira ticket
    ticket = fetch_jira_ticket(state["jira_ticket_id"])

    # 2. DONE gate — immediate exit
    if ticket["status"] == "DONE":
        return {**state, "current_phase": "EXIT", "jira_ticket": ticket}

    # 3. Load skills prompt
    skills = SKILLS_PROMPT.read_text()

    # 4. LLM routing decision
    llm = get_llm(TaskComplexity.MEDIUM)
    routing_prompt = f"""
{skills}

## CURRENT TICKET
{json.dumps(ticket, indent=2)}

## USER PROMPT
{state['user_prompt']}

Based on the ticket complexity, decide the routing.
Return ONLY JSON: {{"routing_decision": "FULL_PIPELINE" | "DIRECT_IMPLEMENTATION", "risk_level": "LOW|MEDIUM|HIGH", "reasoning": "..."}}
"""
    response = llm.invoke(routing_prompt)
    decision = json.loads(response.content)

    return {
        **state,
        "jira_ticket": ticket,
        "routing_decision": decision["routing_decision"],
        "risk_level": decision["risk_level"],
        "current_phase": "ORCHESTRATOR",
        "total_tokens_used": 0,
        "token_budget": int(os.getenv("TOKEN_BUDGET", 500000)),
        "session_id": str(uuid.uuid4()),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
```

## LLM Routing Logic (Routing Decision Rules)
The Orchestrator's `skills/scripts.md` instructs the LLM to route based on:

| Signal | Route To |
|---|---|
| Ticket has >3 affected components mentioned | `FULL_PIPELINE` |
| Ticket risk label = HIGH or P0/P1 | `FULL_PIPELINE` |
| Ticket is a simple config change / copy fix | `DIRECT_IMPLEMENTATION` |
| Ticket description references architecture | `FULL_PIPELINE` |
| Short, single-file bug fix | `DIRECT_IMPLEMENTATION` |

## `skills/scripts.md` Updated
`orchestrator/skills/scripts.md` — fully written with persona, routing rules, DONE-check behavior, token budget behavior, and risk assessment reasoning strategy.

## LangSmith This Milestone
- Named trace: `Orchestrator — Main`
- Child span: `Jira Fetch`
- Child span: `Routing Decision LLM Call`
- LangSmith shows the routing branch taken for each ticket

## Mentor Showcase
Feed a DONE ticket → system exits immediately with a trace. Feed a real ticket → see routing decision with full reasoning in LangSmith. Research/Planner/Executor still stubs.

---

# MILESTONE 3 — Research Agent
**Goal:** The Research Agent fully activates. It uses tools to analyze the codebase and produce structured research output. The first HAG (Human Approval Gateway) is wired.

## Components Built
1. Bash Tool, Grep Tool, AST Tool, Web Search Tool, Build Config Tool
2. Research Agent with ReAct loop
3. HAG via LangGraph `interrupt`
4. `research/skills/scripts.md` fully written

## Tools (`src/tools/`)

```python
# bash_tool.py
import subprocess
from langchain_core.tools import tool

@tool
def bash_command(command: str) -> str:
    """Run a shell command and return stdout. Use for file reads, grep, find."""
    result = subprocess.run(command, shell=True, capture_output=True, text=True, timeout=30)
    return result.stdout or result.stderr

# ast_tool.py — uses tree-sitter for Python/Java/JS parsing
@tool
def ast_analyze(file_path: str, target_symbol: str) -> str:
    """Parse a file's AST and return the dependency tree for a given symbol."""
    # tree-sitter implementation
    ...

# web_search_tool.py
from langchain_community.tools import DuckDuckGoSearchRun

@tool
def web_search(query: str) -> str:
    """Search the web for error messages, API docs, or library references."""
    return DuckDuckGoSearchRun().run(query)
```

## Research Agent with ReAct Loop

```python
from langgraph.prebuilt import create_react_agent
from langsmith import traceable
from src.core.state import AgentState, ResearchOutput
from src.core.llm import get_llm, TaskComplexity
from src.tools.bash_tool import bash_command
from src.tools.ast_tool import ast_analyze
from src.tools.web_search_tool import web_search
from pathlib import Path
import json

SKILLS_PROMPT = Path(__file__).parent / "skills/scripts.md"

RESEARCH_TOOLS = [bash_command, ast_analyze, web_search]

@traceable(name="Research Agent — Full Analysis")
def research_node(state: AgentState) -> AgentState:
    skills = SKILLS_PROMPT.read_text()
    llm = get_llm(TaskComplexity.HIGH)

    # Build ReAct agent with tools
    agent = create_react_agent(llm, RESEARCH_TOOLS)

    research_prompt = f"""
{skills}

## TICKET CONTEXT
{json.dumps(state['jira_ticket'], indent=2)}

## USER PROMPT
{state['user_prompt']}

## PREVIOUS FAILED ATTEMPTS (avoid these approaches)
{json.dumps(state.get('previous_attempts', []), indent=2)}

Analyze the codebase. Produce the ResearchOutput JSON schema.
"""
    result = agent.invoke({"messages": [("user", research_prompt)]})
    research_output = json.loads(result["messages"][-1].content)

    return {
        **state,
        "research_output": research_output,
        "current_phase": "RESEARCH",
    }
```

## HAG Implementation (LangGraph `interrupt`)

```python
# In src/core/graph.py — add after research node
from langgraph.types import interrupt

def research_hag_node(state: AgentState) -> AgentState:
    """Pause execution and wait for human approval."""
    human_decision = interrupt({
        "message": "Research complete. Approve to proceed to Planning.",
        "research_output": state["research_output"],
        "risk_level": state["risk_level"],
    })
    # human_decision = {"status": "APPROVED"} or {"status": "REJECTED", "feedback": "..."}

    gate = {
        "gate_id": f"hag_research_{state['session_id']}",
        "phase": "RESEARCH",
        "status": human_decision["status"],
        "feedback": human_decision.get("feedback"),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return {**state, "human_gates": [*state.get("human_gates", []), gate]}

# Conditional edge after HAG
graph.add_conditional_edges(
    "research_hag",
    lambda s: s["human_gates"][-1]["status"],
    {"APPROVED": "planner", "REJECTED": "research"}   # REJECTED loops back
)
```

## `skills/scripts.md` Updated
`research/skills/scripts.md` — fully written with ReAct loop steps, tool selection criteria, self-healing instructions (read `previous_attempts`), output schema enforcement.

## LangSmith This Milestone
- Named trace: `Research Agent — Full Analysis`
- Each tool call appears as a child span: `bash_command`, `ast_analyze`, `web_search`
- HAG pause/resume is visible as an `interrupt` event in the trace
- You can replay any research run from LangSmith

## Mentor Showcase
Feed a real ticket → watch the Research Agent call tools in sequence in LangSmith → get prompted for HAG approval → on approval, Planner stub fires.

---

# MILESTONE 4 — Planner Agent + Cache Memory
**Goal:** Planning Agent produces a Finalized TODO. Self-healing memory is active. Second HAG is wired.

## Components Built
1. Redis-backed Cache Memory (`src/memory/cache.py`)
2. Planner Agent with self-healing
3. Second HAG (Planning gate)
4. `planner/skills/scripts.md` fully written

## Cache Memory (`src/memory/cache.py`)

```python
import redis, json, os
from src.core.state import FailedAttempt

r = redis.Redis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379"))

def save_failed_attempt(attempt: FailedAttempt):
    key = f"failed:{attempt['ticket_id']}"
    existing = json.loads(r.get(key) or "[]")
    existing.append(attempt)
    r.set(key, json.dumps(existing), ex=60*60*24*30)  # 30-day TTL

def get_failed_attempts(ticket_id: str) -> list[FailedAttempt]:
    raw = r.get(f"failed:{ticket_id}")
    return json.loads(raw) if raw else []
```

The Orchestrator calls `get_failed_attempts(ticket_id)` at startup and populates `state["previous_attempts"]`. The Planner reads this from state — no direct Redis call in the Planner.

## Planner Agent

```python
@traceable(name="Planner Agent — TODO Generation")
def planner_node(state: AgentState) -> AgentState:
    skills = SKILLS_PROMPT.read_text()
    llm = get_llm(TaskComplexity.HIGH)

    planner_prompt = f"""
{skills}

## RESEARCH OUTPUT (your input)
{json.dumps(state['research_output'], indent=2)}

## PREVIOUS FAILED ATTEMPTS (MUST avoid these patterns)
{json.dumps(state.get('previous_attempts', []), indent=2)}

## TICKET
{json.dumps(state['jira_ticket'], indent=2)}

Produce the Finalized TODO as PlanOutput JSON.
Each TodoItem must reference a real file from research_output.affected_files.
"""
    response = llm.invoke(planner_prompt)
    plan_output = json.loads(response.content)

    return {
        **state,
        "plan_output": plan_output,
        "current_phase": "PLANNING",
    }
```

## `skills/scripts.md` Updated
`planner/skills/scripts.md` — fully written with TODO decomposition strategy, self-healing deviation instructions, rollback strategy requirement, and output schema.

## LangSmith This Milestone
- Named trace: `Planner Agent — TODO Generation`
- Child span: `Self-Healing Context Load` (logs what previous attempts were found)
- Second HAG pause/resume visible in trace
- On REJECTED, the feedback + re-plan loop is fully traceable

## Mentor Showcase
Full planning pipeline: Research → HAG1 → Plan → HAG2 → see Finalized TODO in structured JSON. Self-healing visible: run same ticket twice, second run shows "avoiding approach from attempt_001."

---

# MILESTONE 5 — Executor Agent + MR Generation
**Goal:** The system resolves a real ticket end-to-end. Git Worktree is isolated. MR is raised automatically.

## Components Built
1. Git Worktree Tool
2. Executor Agent (strictly dumb — no reasoning, only executes TODO)
3. MR generation via Git CLI / GitHub API
4. `executor/skills/scripts.md` fully written

## Git Worktree Tool

```python
# src/tools/git_worktree_tool.py
import subprocess, os
from langchain_core.tools import tool

@tool
def create_worktree(repo_path: str, branch_name: str) -> str:
    """Create an isolated git worktree for safe code modification."""
    worktree_path = f"/tmp/worktrees/{branch_name}"
    subprocess.run(
        ["git", "worktree", "add", "-b", branch_name, worktree_path],
        cwd=repo_path, check=True
    )
    return worktree_path

@tool
def commit_and_push(worktree_path: str, branch_name: str, commit_msg: str) -> str:
    """Stage all changes, commit, and push to remote."""
    subprocess.run(["git", "add", "-A"], cwd=worktree_path, check=True)
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=worktree_path, check=True)
    subprocess.run(["git", "push", "origin", branch_name], cwd=worktree_path, check=True)
    return branch_name

@tool
def raise_mr(branch_name: str, ticket_id: str, description: str) -> str:
    """Create a Merge Request via GitLab/GitHub API."""
    # GitLab API call using GITLAB_TOKEN env var
    import requests
    response = requests.post(
        f"{os.getenv('GITLAB_URL')}/api/v4/projects/{os.getenv('PROJECT_ID')}/merge_requests",
        headers={"PRIVATE-TOKEN": os.getenv("GITLAB_TOKEN")},
        json={
            "source_branch": branch_name,
            "target_branch": "main",
            "title": f"[AUTO] Fix for {ticket_id}",
            "description": description,
        }
    )
    return response.json()["web_url"]
```

## Executor Agent

```python
@traceable(name="Executor Agent — Implementation")
def executor_node(state: AgentState) -> AgentState:
    """
    DUMB executor. Zero reasoning. Strictly follows TodoItem list in order.
    If a step fails, it logs the error and stops — does NOT attempt to reason about it.
    """
    skills = SKILLS_PROMPT.read_text()
    llm = get_llm(TaskComplexity.MEDIUM)

    todo_list = state["plan_output"]["todo_list"]
    repo_path = os.getenv("REPO_PATH")
    branch_name = f"auto-fix/{state['jira_ticket_id'].lower()}-{state['session_id'][:8]}"

    # Create isolated worktree
    worktree_path = create_worktree.invoke({"repo_path": repo_path, "branch_name": branch_name})

    # Execute each TodoItem in strict order
    for todo_item in todo_list:
        execution_prompt = f"""
{skills}

## YOUR ONLY JOB
Execute this single TodoItem. Nothing else.
{json.dumps(todo_item, indent=2)}

## WORKTREE PATH
{worktree_path}

Execute the command_sequence exactly. Return JSON: {{"success": bool, "output": "..."}}
"""
        result = llm.invoke(execution_prompt)
        step_result = json.loads(result.content)

        if not step_result["success"]:
            # Log failure — Orchestrator will handle self-healing
            return {
                **state,
                "current_error_log": step_result["output"],
                "current_phase": "IMPLEMENTATION",
            }

    # All steps passed — raise MR
    mr_url = raise_mr.invoke({
        "branch_name": branch_name,
        "ticket_id": state["jira_ticket_id"],
        "description": state["plan_output"]["file_edit_strategy"],
    })

    return {
        **state,
        "execution_output": {
            "mr_url": mr_url,
            "changed_files": [t["file_path"] for t in todo_list],
            "git_diff_summary": "Auto-generated",
            "worktree_path": worktree_path,
            "compilation_status": "PASS",
        },
        "current_phase": "REVIEW",
    }
```

## `skills/scripts.md` Updated
`executor/skills/scripts.md` — persona explicitly states "You do NOT reason. You do NOT deviate. You execute the TODO list step by step and report success or failure per step."

## LangSmith This Milestone
- Named trace: `Executor Agent — Implementation`
- Each TodoItem execution is a child span
- Worktree creation, commit, push, MR raise all appear as tool spans
- Full end-to-end trace from Jira fetch to MR URL is now visible in one LangSmith run

## Mentor Showcase
**Full ticket-to-MR demo.** Show the MR URL in the terminal. Show the full trace in LangSmith — Jira fetch → routing → research → plan → execution → MR.

---

# MILESTONE 6 — CI/CD Error Loop + Production Hardening
**Goal:** System is self-healing on CI/CD failures. K8s is fully deployed. LLM is swapped to production Anthropic API.

## Components Built
1. CI/CD error ingestion endpoint
2. Error-to-Orchestrator routing loop
3. Cache Memory write on failure
4. K8s full deployment manifests
5. Production `.env` finalized

## CI/CD Error Loop

```python
# New endpoint: POST /webhook/cicd-error
# Receives raw error log from CI/CD pipeline after developer manually triggers

@app.post("/webhook/cicd-error")
async def cicd_error_webhook(payload: CICDErrorPayload):
    # 1. Save failed attempt to Redis
    failed_attempt = FailedAttempt(
        attempt_id=str(uuid.uuid4()),
        ticket_id=payload.ticket_id,
        phase_failed="CICD",
        error_summary=payload.error_log[:2000],  # Truncate for Redis
        plan_used=payload.plan_used,
        timestamp=datetime.now(timezone.utc).isoformat(),
    )
    save_failed_attempt(failed_attempt)

    # 2. Reconstruct state and re-invoke Orchestrator
    state = restore_session(payload.session_id)  # From MongoDB
    state["current_error_log"] = payload.error_log
    state["retry_count"] += 1

    if state["retry_count"] > state["max_retries"]:
        return {"status": "MAX_RETRIES_EXCEEDED", "action": "manual_intervention_required"}

    # 3. Re-trigger graph from Orchestrator
    graph = build_graph()
    graph.invoke(state)
    return {"status": "SELF_HEALING_INITIATED"}
```

## K8s Full Deployment (`k8s/deployment.yaml`)

```yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: jira-solver
spec:
  replicas: 2
  selector:
    matchLabels:
      app: jira-solver
  template:
    metadata:
      labels:
        app: jira-solver
    spec:
      containers:
      - name: jira-solver
        image: sprinklr/jira-solver:latest
        envFrom:
        - configMapRef:
            name: jira-solver-config
        - secretRef:
            name: jira-solver-secrets
        resources:
          requests:
            memory: "512Mi"
            cpu: "250m"
          limits:
            memory: "2Gi"
            cpu: "1000m"
```

## Production LLM Swap
Change one line in K8s ConfigMap:
```yaml
LLM_PROVIDER: "anthropic"   # was "local"
```
Zero code changes. All agents pick up the new provider via `get_llm()`.

## LangSmith This Milestone
- Separate LangSmith project for production: `sprinklr-jira-solver-prod`
- Self-healing re-runs are linked to the original session trace via `session_id`
- Retry count visible in every trace as metadata tag
- Set up LangSmith alerts for traces where `retry_count > 2`

## Mentor Showcase
**Production demo.** Show the K8s pods running. Simulate a CI/CD failure. Watch the error hit the webhook, write to Redis, and trigger a new self-healing run — full trace in LangSmith prod project.

---

## Milestone Summary

| Milestone | Deliverable | Showcase |
|---|---|---|
| **M1** | Docker + K8s stubs + State schema + LangSmith + LLM abstraction + graph skeleton | Full graph visible in LangSmith with stub traces |
| **M2** | Orchestrator Agent + Jira tool + routing logic | DONE-gate works; routing decision with LLM reasoning traced |
| **M3** | Research Agent + tools + HAG #1 | First real agent doing codebase analysis; HAG pause/resume traced |
| **M4** | Planner Agent + Cache Memory + HAG #2 | Self-healing in action; Finalized TODO produced |
| **M5** | Executor Agent + Git Worktree + MR generation | First full ticket → MR demo end-to-end |
| **M6** | CI/CD error loop + K8s production + LLM swap | Production-grade self-healing system fully deployed |

---

*Document Version: 1.0 | Project: Sprinklr Jira Solver | Role: Lead Architect Reference*
