# IntuitionX — Architecture Presentation Pack

Copy Mermaid blocks into [mermaid.live](https://mermaid.live) → Export SVG/PNG for slides.  
Bullet lines are ≤8 words for deck text; speaker notes are for you.

---

## Slide A1 — One-liner Architecture

**Says:**
- Ticket in → reviewed PR out
- LangGraph owns phase topology
- Four agents; skills own intelligence
- Cursor edits; LLM routes & validates
- Human gates pause before write/PR

**Speaker:** IntuitionX is a multi-agent LangGraph pipeline. Python wires phases; markdown skills decide routing and fix patterns; Cursor applies code changes in an isolated worktree.

---

## Slide A2 — System Context (C4 Level 1)

**Says:**
- Inputs: Jira ticket + optional repo path
- Entrypoints: FastAPI `/run` or Chainlit
- Brain: Sprinklr IntuitionX LLM router
- Hands: Cursor headless local agent
- Outputs: GitHub PR + LangSmith traces

```mermaid
flowchart LR
    subgraph External
        Jira[Jira / Cycode ticket]
        GH[GitHub repo]
        Human[Reviewer]
    end

    subgraph IntuitionX
        API[FastAPI / Chainlit]
        LG[LangGraph pipeline]
        Skills[skills/*.md]
        Redis[(Redis jobs)]
    end

    subgraph Providers
        LLM[Sprinklr LLM router]
        Cursor[cursor-sdk Agent]
        LS[LangSmith]
    end

    Jira --> API
    Human -->|approve / revise| API
    API --> Redis
    API --> LG
    LG --> Skills
    LG --> LLM
    LG --> Cursor
    Cursor --> GH
    LG --> LS
    LG -->|PR URL| GH
```

---

## Slide A3 — Layered Stack

**Says:**
- UI/API — admit tickets, HITL
- LangGraph — state machine + gates
- Agents — thin Python runners
- Skills — markdown decision logic
- Integrations — Jira, Cursor, Redis

```mermaid
flowchart TB
    L1["Entry: FastAPI · Chainlit"]
    L2["Orchestration: src/core/graph.py + AgentState"]
    L3["Agents: orchestrator · research · planner · executor"]
    L4["Skills: skills/<agent>/*.md + skills/security/"]
    L5["Tools: L1 read/grep/bash · CursorGateway"]
    L6["Infra: Redis · worktrees · LangSmith"]

    L1 --> L2 --> L3 --> L4
    L3 --> L5 --> L6
```

| Layer | Owns | Does not own |
|-------|------|--------------|
| Python runners | Call LLM/Cursor, validate JSON, mutate state | Routing rules, fix recipes |
| Skills markdown | When to FULL/MICRO/EXIT, how to fix CVE class | Graph edges, concurrency |
| Cursor | Search + semantic edits in repo | Routing, HITL, PR policy |
| LangGraph | Phase order, interrupts, resume | Prompt wording |

---

## Slide A4 — End-to-End Pipeline (main diagram)

**Says:**
- Orchestrator routes only — no code
- FULL → research → Gate 1 → planner
- MICRO → planner (skip research)
- Gate 2 / micro-confirm → executor
- Executor worktree → edit → PR

```mermaid
flowchart TD
    START([Ticket submitted]) --> ORCH[Orchestrator]
    ORCH -->|EXIT| ENDX([END — no work])
    ORCH -->|FULL| RES[Research]
    ORCH -->|MICRO| PLAN[Planner]
    RES --> G1{Gate 1 — research HAG}
    G1 -->|APPROVE| PLAN
    G1 -->|REVISE| RES
    G1 -->|REJECT| END1([END])
    PLAN --> ROUTE{route?}
    ROUTE -->|MICRO| MC{micro-confirm}
    ROUTE -->|FULL| G2{Gate 2 — plan HAG}
    MC -->|APPROVE| EXEC[Executor]
    MC -->|REVISE| PLAN
    G2 -->|APPROVE| EXEC
    G2 -->|REVISE| PLAN
    G2 -->|REJECT| END2([END])
    EXEC --> PR([GitHub PR])
```

**Talk track:** Point left-to-right. “Triage nurse” = orchestrator. Research only when file location is unclear. Humans see research and/or plan before any PR.

---

## Slide A5 — Routing Decision Tree (Gate 0–3)

**Says:**
- Gate 0 — EXIT if Done / out of scope
- Locatability — file named in ticket?
- Pattern-mirroring — MICRO vs FULL
- Mandatory `reasoning` cites evidence
- Route is auditable, not vibes

```mermaid
flowchart TD
    T[Ticket + context] --> G0{Done / out of scope?}
    G0 -->|yes| EXIT[EXIT]
    G0 -->|no| LOC{File / path in ticket?}
    LOC -->|no| FULL[FULL — needs research]
    LOC -->|yes| PM{Pattern-mirroring MICRO?}
    PM -->|yes| MICRO[MICRO — skip research]
    PM -->|no| FULL
```

| Route | Research? | Typical ticket |
|-------|-----------|----------------|
| **FULL** | Yes | JWT across files; path not obvious |
| **MICRO** | No | Ticket names `api_views/users.py` |
| **EXIT** | No | Already Done / not actionable |

---

## Slide A6 — Agent Roles + Who Calls What

**Says:**
- Orchestrator — one LLM routing turn
- Research — LLM ReAct + Cursor search
- Planner — Cursor Ask → ExecutionPlan
- Executor — Cursor edit + LLM validate
- Cost rule — cheap tools first

```mermaid
flowchart LR
    subgraph Orchestrator
        O1[fetch Jira] --> O2[clone/profile repo] --> O3[LLM → RoutingDecision]
    end

    subgraph Research
        R1[LLM loop] --> R2[L1 tools]
        R1 --> R3[cursor_search ≤2]
        R1 --> R4[ResearchOutput]
    end

    subgraph Planner
        P1[Cursor Ask mode] --> P2[ExecutionPlan JSON]
    end

    subgraph Executor
        E1[git worktree] --> E2[Cursor batch edits]
        E2 --> E3[SHA + LLM validate]
        E3 --> E4[open PR]
    end
```

| Agent | Primary brain | Max Cursor use |
|-------|---------------|----------------|
| Orchestrator | Sprinklr LLM | none |
| Research | LLM + Cursor search | 2 searches (budget) |
| Planner | Cursor Ask | 1 plan call |
| Executor | Cursor edit + LLM judge | ≤2 batch passes |

---

## Slide A7 — Data Flow Through AgentState

**Says:**
- One shared AgentState backpack
- Orchestrator fills ticket + route
- Research fills ResearchOutput
- Planner fills PlanOutput / steps
- Executor fills ExecutionOutput + PR

```mermaid
flowchart LR
    S0[initial_state] --> S1[jira_ticket · routing_route · repo_path]
    S1 --> S2[research_output]
    S2 --> S3[plan_output · execution_steps]
    S3 --> S4[execution_output · mr_url]
    S1 -. human_gates .-> S2
    S3 -. human_gates .-> S4
```

**Key typed payloads:**
- `RoutingDecision` → `FULL` | `MICRO` | `EXIT`
- `ResearchOutput` → hypotheses + affected_files
- `ExecutionPlan` → READ / EDIT / VERIFY steps (semantic instructions)
- `ExecutionOutput` → mr_url, changed_files, worktree

---

## Slide A8 — Skills Progressive Disclosure

**Says:**
- L1 — YAML frontmatter catalog (cheap)
- L2 — skill body when agent needs it
- L3 — references via `load_skill`
- Teams add `skills/security/*.md`
- Python stays thin — skills plug in

```mermaid
flowchart TB
    subgraph Catalog
        FM[YAML frontmatter only]
    end
    subgraph Trigger
        BODY[Full skill body in system prompt]
    end
    subgraph OnDemand
        REF[references/*.md via load_skill]
    end
    FM -->|agent selected| BODY
    BODY -->|deeper pattern| REF
```

**Directory map for slides:**
```
skills/
  orchestrator/orchestrator.md      ← Gate 0–3 tree
  research/research.md              ← search budget
  planner/planner.md + references/  ← plan schema
  executor/executor.md              ← batch + validate
  security/*.md                     ← OWASP fix patterns
```

---

## Slide A9 — Executor Self-Healing Loop

**Says:**
- Pass 1 — batch all EDIT steps
- Validate — SHA + structural + LLM
- Pass 2 — repair failed steps only
- Still failing → hard exit (no PR)
- Default: `CURSOR_MAX_EDIT_ATTEMPTS=2`

```mermaid
flowchart TD
    START[Plan APPROVED] --> WT[Create git worktree]
    WT --> B1[Cursor batch pass 1 — all EDITs]
    B1 --> V1{Validate each step}
    V1 -->|all pass| PR[Commit + open PR]
    V1 -->|some fail| B2[Cursor batch pass 2 — failed only]
    B2 --> V2{Re-validate}
    V2 -->|pass| PR
    V2 -->|fail| ERR[RuntimeError — no silent ship]
```

**Demo callout:** Golden case G-06 needed `retry_count: 1` (pass-2 repair).

---

## Slide A10 — Concurrency & Isolation

**Says:**
- Redis job store + status events
- `Semaphore(2)` caps parallel runs
- Same ticket in-flight → HTTP 409
- One worktree directory per ticket
- Bounds Cursor + LLM spend

```mermaid
sequenceDiagram
    participant C as Client
    participant API as FastAPI
    participant R as Redis
    participant W as Worker
    participant G as LangGraph

    C->>API: POST /run {ticket_id}
    API->>R: check in-flight
    alt duplicate ticket
        API-->>C: 409 Conflict
    else new job
        API->>R: enqueue job
        API-->>C: job_id
        W->>R: acquire semaphore
        W->>G: run_graph_until_pause
        G-->>W: pause (gate) or done
        W->>R: update status / events
        C->>API: GET /tickets/{id}
        C->>API: POST /approve/{thread_id}
    end
```

---

## Slide A11 — Evaluation Architecture (optional)

**Says:**
- Golden cases G-01–G-18 = contract
- Route + file F1 + LLM judges
- Pass = executor judge true
- Pass 3 baseline: 18/18 (100%)
- Proves architecture choices hold

```mermaid
flowchart LR
    GC[Golden case] --> R[Route eval]
    GC --> F[Research file_f1]
    GC --> PJ[Planner judge]
    GC --> EJ[Executor judge]
    EJ -->|passed| PASS[PASS]
    EJ -->|failed / abort| FAIL[FAIL + taxonomy]
```

| Metric | What it proves about architecture |
|--------|-----------------------------------|
| Route correctness | Gate 0–3 tree is teachable |
| file_f1 | Research + Cursor search find vulns |
| Planner/Executor judge | Semantic plans + validation work |
| Pass rate 16.7% → 100% | Symbol-resolver → skills + SHA repair |

---

## Slide A12 — Why This Design (speak to)

1. **Skills-as-config** — change routing/fix text without redeploying agent Python.
2. **Multi-agent, not monolith** — separate LangSmith audits; fail one phase cleanly.
3. **HITL gates** — humans approve research/plan before Cursor writes a PR.
4. **Worktree isolation** — ticket blast radius = one directory / branch.
5. **Budgeted Cursor** — expensive searches/edits capped; L1 tools do cheap read/grep.

---

## Paste-ready bar chart data (token / quality)

Use in Excel or slide chart:

| Series | Value |
|--------|------:|
| Research call-1 tokens (before progressive tools) | 7587 |
| Research call-1 tokens (after) | 2500 |
| Tool-schema tokens (before) | 4814 |
| Tool-schema tokens (after) | ~450–900 |
| Benchmark Pass 1 | 16.7% |
| Benchmark Pass 2 | 72.2% |
| Benchmark Pass 3 | 100% |
| Planner judge mean (Pass 3) | 94.9 |
| Executor judge mean (Pass 3) | 96.8 |

---

## 60-second architecture script

> “A Jira security ticket hits FastAPI or Chainlit. LangGraph starts with the orchestrator—one LLM call that only chooses FULL, MICRO, or EXIT. FULL tickets go through research with cheap in-process tools and at most two Cursor searches, then a human Gate 1. MICRO skips research because the ticket already names the file. The planner asks Cursor for a semantic ExecutionPlan; Gate 2 or micro-confirm pauses for approval. The executor clones into a ticket worktree, applies edits in up to two Cursor batches, validates with SHA plus an LLM security check, and opens a GitHub PR. Routing intelligence and fix recipes live in markdown skills—so teams extend the system without rewriting the pipeline.”
