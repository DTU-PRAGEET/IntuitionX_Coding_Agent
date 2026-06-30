---
name: raise-mr
description: Use this skill to push the current Sprinklr backend branch to GitLab and create or update a merge request against origin/master with a structured title, summary, implementation notes, and test plan. Trigger when the user asks to raise an MR, update an MR, push for review, prepare a GitLab MR description, or refresh an existing MR after new commits.
---

# /raise-mr

Push the current branch to GitLab and create or update a Merge Request with a structured description.

---

## Steps

### 1 — Gather context

**Skip this step if you already have the diff and commit list in context** (e.g. from a prior `/code-review` run in this session).

Otherwise:
```
git log origin/master..HEAD --no-merges --format="%h %s"
git diff origin/master...HEAD --stat
git diff origin/master...HEAD
```

Use `origin/master` as the review base. If `origin/master` is unavailable, stop and report that the base ref is missing instead of switching to another base.

**Extract the Jira ticket ID(s) from commit messages.**
Every commit follows the format `CARE-1234 | <description>`.
Take the ticket ID prefix from commits.

If no ticket ID is found in any commit message, ask the user:
> What is the Jira ticket ID for this MR? (e.g. CARE-12345)

Before any side effects, print a short preflight summary:
```
Branch: <current branch>
Base: origin/master
Ticket: <ticket id>
Mode: create or update (if already known)
Files changed: <count>
```

If the branch, base, or ticket looks wrong, ask the user before pushing.

---

### 2 — Push and detect MR status

Push the branch:
```
git push origin $(git branch --show-current) 2>&1
```

Parse the push output:
- If GitLab prints a `remote: Create merge request for ...` link → **no MR exists yet**, use that URL to create one (go to step 3a)
- If GitLab prints a `remote: View merge request for ...` link → **MR already exists**, extract the MR IID from the URL (go to step 3b)
- If the push output has **no MR link** (for example, "Everything up-to-date"), query existing MRs:
  ```
  glab mr list --source-branch "$(git branch --show-current)" --state opened
  ```
  - If an MR exists, use its IID and go to step 3b.
  - If no MR exists, continue with step 3a and create one.

---

### 3a — Create flow: generate MR description

Analyze the full diff and commit history to produce the description below.

```markdown

## Summary

- <bullet 1>
- <bullet 2>
- <bullet 3 if needed>

## Changes

<Overview of changes>

## Test plan

- [x] <Test file path>
  - <testName1>
  - <testName2>
```

**Summary** — *what and why* only
- Describe type - Enhancement / Bug Fix (or both if applicable)
- State the behaviour added, fixed, or removed and the business reason for it
- No class names, method names, or implementation detail — the reviewer will read the code
- Keep it short & precise, ensuring easy understanding of change & intent without dwelling into any implementation detail.
- Never include bug fixes that arose during code review — those are noise at the summary level
- Do not mention tests here (tests belong only in **Test plan**)

**Changes** — *how* only;
- For non-trivial changes, explain the implementation details:
  - A brief overview that would help the reviewer develop a mental map, before reading the actual code.
  - Add file/method name references wherever needed, but avoid over-explanation.  
  - Skip trivial information like : "Added constants to file XYZ", "Added null check in FileX#methodY" unless they are the only change in MR
- Do not mention tests here (tests belong only in **Test plan**)

**Test plan** — only place where tests appear:
- List each **test file** touched/relied on. Use `[x]` prefix.
- Under each file, list the specific test methods added/modified (or run) as sub-bullets. 

Write the generated description to a temporary markdown file, for example `/tmp/<branch>-mr-description.md`. Do not inline a multi-line generated description directly into the shell command.

Then create the MR:
```bash
MR_DESCRIPTION_FILE="/tmp/<branch>-mr-description.md"
MR_DESCRIPTION="$(cat "$MR_DESCRIPTION_FILE")"

glab mr create \
  --title "<TICKET> | <description>" \
  --description "$MR_DESCRIPTION" \
  --target-branch master \
  --remove-source-branch \
  --squash
```

Remove `--squash` if the branch has only one commit.

The MR URL will be on `https://prod-gitlab.sprinklr.com/sprinklr/main/sprinklr.app/-/merge_requests/<iid>`.

After creating the MR, always assign it to the authenticated `glab` user:
```bash
# Get current user id
glab api "/user" | jq '{id, username}'

# Assign the MR (extract <iid> from the created MR URL)
glab api --method PUT "/projects/198/merge_requests/<iid>" --field assignee_id=<id> | jq '{iid, assignee: (.assignee.username // null)}'
```

---

### 3b — Update flow: rewrite full description

The MR already exists. Rewrite the entire description from scratch based on the complete current diff and all commits — not just the new ones. The reviewer reads the description as a unified document, not a changelog.

Fetch the current MR to check the title:
```bash
glab api "/projects/198/merge_requests/<iid>" | jq '{title, description}'
```

Regenerate the full description using the same template as step 3a, incorporating everything across all commits. Save it to a temporary markdown file instead of passing a handwritten multi-line string inline. Then update:
```bash
MR_DESCRIPTION_FILE="/tmp/<branch>-mr-description.md"
MR_DESCRIPTION="$(cat "$MR_DESCRIPTION_FILE")"

glab mr update <iid> \
  --title "<TICKET> | <description>" \
  --description "$MR_DESCRIPTION"
```

Only update the title if it does not already follow the correct format (`<TICKET> | <description>`).

---

### 4 — Derive the MR title

Format: `<TICKET> | <concise imperative description>`

Examples:
- `CARE-79277 | Mask PII data in GenAI log statements using PIILogMaskingUtils`
- `CARE-81234 | Add rate limiting to copilot message handler`

Rules:
- Lead with the Jira ticket ID and `|` separator
- Use an action verb: Mask, Add, Fix, Refactor, Remove, Extend
- Title case after the separator
- Max 72 characters after the ticket prefix
- No branch names, no implementation jargon

---

### 5 — Output

```
✅ MR created: https://prod-gitlab.sprinklr.com/sprinklr/main/sprinklr.app/-/merge_requests/<iid>
   Title:  <TICKET> | <description>
   Branch: <branch> → master
   Files:  N files changed
```

Or for updates:
```
✅ MR updated: https://prod-gitlab.sprinklr.com/sprinklr/main/sprinklr.app/-/merge_requests/<iid>
   Title:  <TICKET> | <description>
   Description rewritten from full diff across N commits.
```

After a successful create/update, ask whether to move the Jira ticket to In Dev Review using `/move-ticket`.

---

## Attribution Rules

- Never add `Co-Authored-By`, `Generated-with`, or any AI/tool attribution to MR titles, descriptions, or commit messages on this branch
- Do not mention AI assistants (Cursor, Claude, Codex, ChatGPT, etc.) anywhere in the MR

---

## glab Rules

- Project: `sprinklr/main/sprinklr.app` (id=`198`) on `prod-gitlab.sprinklr.com`
- Never use `:id` placeholder in `glab api` calls — always use the hardcoded project id `198`
- Never put the HTTP verb as a positional argument (`glab api GET /path` → error). Use `--method` for non-GET
- Always pipe `glab api` output through `jq` immediately — never dump raw JSON to the shell
- `glab mr create/update/view` are CLI commands and do not use `--method`; `glab api` is for raw REST calls

---

## Description Quality Rules

- **Default to short**: short bullets + short Changes sentence; longer explanation only for complex diffs
- **Summary** is what changed and why — never how; no class or method names; no tests
- **Changes** is a brief explanation of approach — not a per-file changelog; no tests
- **Test plan** is the only place to list tests; enumerate test files with `[x]` and list test methods under them
- If no tests were run, include a single `[x] Not run (<reason>)` entry
- Active voice throughout — "Added X", "Fixed Y", "Removed Z"
- If a reviewer learns nothing from the description that they wouldn't from the diff, it is too detailed — cut it
 
 ---
name: write-tests
description: Writes and updates unit tests alongside production code so diff line coverage meets CI (aim ≥90% on changed lines; pipeline validates diff coverage and requires *Test.java in the MR). Use when implementing or editing Java in this repo, when the user asks for tests, coverage, or MR-ready changes.
---

# Sprinklr unit tests

## When to apply

After adding or changing non-test Java (or when asked for tests/coverage): add or extend tests in the same MR so CI diff coverage and the mandatory test-file check pass.

## Find what changed

- If the user named specific files, write tests only for the **modified lines** in those files; get the ranges with `bash .agents/skills/write-tests/scripts/changed_java.sh <file>...`.
- Otherwise auto-detect changed production files and their modified line ranges with `bash .agents/skills/write-tests/scripts/changed_java.sh`. Skip if the full change set is already in context.
- The script reports working-tree changes vs `HEAD` (staged + unstaged) and excludes test files, generated sources, and unversioned/untracked files in auto mode. Output is `path/to/File.java: <start-end> …`. Do not write tests for unchanged behavior.

## Coverage and CI gates (non-negotiable)

- Diff coverage: CI measures line coverage on changed lines between the MR branch and target. Aim ≥90%; documented minimum is 85% (per-cloud). Exclude trivial getters/setters.
- Mandatory test file: at least one changed file matching `*Test.java` or `*Tests.java`.
- Do not add `@SkipGeneratedTestCoverage`; the pipeline blocks it.

## Where tests live

- Default: `test/src/test/java/`, mirroring the class package (`com.spr.foo.BarImpl` → `com.spr.foo.BarTest`).
- Some modules own their tests (e.g. GraphQL → `graphql/src/test/java/`); follow the existing location for the area you change.
- Prefer updating existing tests over creating new ones. Register a new test class in its module test suite (e.g. `PlatformTestSuite`).

## Base class and stack

- First evaluate what mocking the test actually needs, then extend only the most suitable base stack from `MockedUnitTest` -> `InfraElasticsearchMockedUnitTest` -> `MockedCoreUnitTest` -> `BaseSprTest` (each layer adds capabilities and overhead), or the most specific existing `Abstract*Test` / `Base*Test` for the area; reuse its hooks instead of re-wiring `@Before`. Do not extend a base stack you don't need — if the test only exercises static methods or pure logic with no mocking, embedded infra, or Spring context, extend none of these and write a plain JUnit 4 test.
- JUnit 4 only: `org.junit.Test`, `@Before`, `@After`, `Assert`, `Rule`.
- Mockito 1.10.8: `org.mockito.Matchers` (not `ArgumentMatchers`); `invocationOnMock.getArgumentAt(index, Class)` (not `getArgument`).
- Module-specific bases: see the `## References` section for specific Sprinklr module bases.

## Mocking

| Need | Use |
|------|-----|
| Real spied instance with deps resolved, `@PostConstruct`, registered in Spring | `mock(MyImpl.class)` |
| Pure Mockito mock | `mockPlain(MyService.class)` |
| Register for `ServiceLocatorFactory` / `@Autowired` | `putSpringBean` / `addSpringBean` / `putSpringMock` |
| Replace an already-registered mock | `reAddMock(Class)` |
| Stubbing | `Mockito.doReturn` / `doAnswer` / `doThrow` … `.when(mock).method(...)` — never `when(mock.method()).thenReturn(...)` |

Do not create dummy or stub inner implementations; use `mock` / `mockPlain`. Use reflection only as a last resort when public or package-private entrypoints cannot reach the changed lines.

## Embedded infra

- MongoDB: never mock `mongoTemplateFactory` or `mongoTemplate`; use `mongoTemplateFactory.getMongoTemplate(TEST_PARTNER_ID)` and real CRUD.
- Elasticsearch: call `setUpESRepo(TEST_PARTNER_ID, ESServerType.…, Version.V_7_0_0)` before ES-backed code; do not stub `ReportingEngine` query/aggregate paths — use embedded ES with real data.

## Setup and context

- `@Before` / `@After`: name `doSetup…` / `doCleanup…`.
- Default context is auto-set (`TEST_PARTNER_ID`, `TEST_CLIENT_ID`, `TEST_USER_ID`). Use `executeInContext(userId, clientId, partnerId, runnable)` for other contexts.
- Dynamic props: `setDynamicProperty(…)`. Static props: `setSprProperty` / `unsetSprProperty` (unset in cleanup).
- Statics: `UnitTestUtils.setStaticFieldValue` and reset in `@After` / `postprocess()`; `UnitTestUtils.resetServiceLocatorFactory()` to clear cached beans.

## Service under test

Prefer `new MyServiceImpl(deps…)` with `mockPlain` dependencies when construction is simple; use `mock(MyServiceImpl.class)` when autowiring is heavy.

## Test design

For every changed or new method, cover each if/else branch, each switch case (including default), the try-success path and each catch (trigger real exceptions), and each reachable return. Use AAA (Arrange / Act / Assert). Name methods `testMethod_scenario` or `shouldOutcome_whenCondition`. Keep tests order-independent.

## Quality bar

- FIRST: Fast (no real network or sleeps), Independent (no order or shared-state coupling), Repeatable (deterministic — inject clocks/ids; no `new Date()`, `Random`, or `Thread.sleep` in assertions), Self-validating, Timely.
- Assert observable outcomes: return values, state changes, persisted docs, emitted events, or `verify` on real collaborators. "Runs without throwing" counts only when not-throwing is the contract.
- Test behavior, not implementation. Avoid over-mocking and pinning call counts/order unless that is the contract.
- One logical concept per test. Cover equivalence classes and boundaries: null/empty, single vs many, min/max, off-by-one, duplicate, and each error path.
- No logic in tests; use explicit expected values.

## Flag logical inconsistencies

When code behavior contradicts its apparent intent (a validator that silently returns a default, an inverted condition, an unreachable branch, a missing switch default, a return that cannot be hit): stop, surface it (file, method, intended vs. actual, triggering input), and propose a fix or ask via `AskQuestion`. Do not write a green test that asserts the buggy behavior. Coverage is never a reason to lock in a defect.

## Do not

- Mock loggers, `PerfTracker`, or `PerfStats`.
- Test private methods directly unless unavoidable.
- Use JUnit 5 or Mockito `ArgumentMatchers`.
- Write tests for unchanged behavior.

## Run and verify

- Run the smallest relevant test surface (the specific updated or added classes).
- For changed-line coverage: if IntelliJ MCP is active, use `intellij_run_test_coverage` → `intellij_get_task` → `intellij_diff_test_coverage(revision="HEAD")`; otherwise run the smallest test surface and rely on CI to validate diff coverage.
- If the coverage toolchain is blocked (e.g. IntelliJ coverage fails), report the exact blocker; do not estimate or claim the gate passed.

## Workflow checklist

1. Identify changed production types (run the `changed_java.sh` script on the working tree, or the named files).
2. Locate existing tests and prefer updating; otherwise create the mirrored `*Test.java` and register it in the module suite.
3. Pick the most specific base; for reporting or GraphQL changes read the matching reference file.
4. Flag any logical inconsistency instead of asserting buggy behavior.
5. Cover branches/returns/exceptions with meaningful assertions; run tests and verify the coverage gate.
6. Confirm the MR includes a `*Test(s).java` change and no `@SkipGeneratedTestCoverage`.

## References

- `references/unit-test-guidelines.md` — detailed mocking, helper methods, coverage computation, quick-start template.
- `references/reporting/reporting-tests.md` — reporting and `Measurement`/`Dimension` metadata base classes.
- `references/graphql/graphql-tests.md` — GraphQL base class.
 