# Cursor SDK integration

Headless programmatic agents via `cursor-sdk`.

## Setup

```bash
export CURSOR_API_KEY="cursor_..."   # Cursor Dashboard → Integrations
pip install cursor-sdk
```

Without `CURSOR_API_KEY`, use dry-run mode on `CursorGateway` to validate wiring only.

## Research search workflow

The Research Agent delegates repository navigation to **`CursorGateway.search()`** through the **`cursor_search`** LangChain tool.

```
ResearchAgent → cursor_search → CursorGateway.search() → cursor-sdk → structured hits
```

Documentation: [`src/agents/research/SEARCH_WORKFLOW.md`](../agents/research/SEARCH_WORKFLOW.md)

Migration notes: [`MIGRATION.md`](MIGRATION.md)
