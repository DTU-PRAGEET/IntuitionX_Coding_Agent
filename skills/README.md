# Team Skills Registry

Markdown-declared skill modules loaded at runtime via `SkillRegistry` without changing `/core` or `/agents` Python code.

## Structure

```
skills/
  <module-name>/
    <skill-name>.md   ← skill definition + test cases
    README.md         ← module-specific docs
    references/       ← optional supporting docs
```

## Adding a skill

1. Create `skills/<module>/<skill-name>.md` with YAML frontmatter and named sections.
2. Set `agents: [research, planner, implement]` to control which agents receive it.
3. Set `trigger_conditions` so the registry selects it for matching tickets.
4. Restart or call `SkillRegistry.reload()` — no Python changes required.

## Reference skills (M1.4)

- `skills/security/java-sast-fixes.md` — Java SAST remediation patterns
- `skills/security/java-sca-fixes.md` — Java SCA / CVE dependency fixes

## Agent base skills

| Module | File | Agent |
|--------|------|-------|
| orchestrator | orchestrator.md | Orchestrator |
| research | research.md | Research |
| planner | planner.md | Planner |
| executor | executor.md | Implementation |

Skills are injected into agent prompts via `SkillRegistry.select()` and `format_for_prompt()`.
