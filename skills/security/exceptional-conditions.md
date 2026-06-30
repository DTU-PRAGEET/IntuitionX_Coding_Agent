---
name: exceptional-conditions
module: security
description: Classify and remediate Mishandling of Exceptional Conditions — verbose errors, fail-open, swallowed exceptions
agents: [research, planner, implement]
trigger_labels: exceptional-conditions, error-handling, exception-handling, verbose-errors, fail-open, stack-trace
---

# Mishandling of Exceptional Conditions (OWASP A10)

## Definition

Errors and exceptions are handled in ways that weaken security — verbose disclosure, fail-open on auth failures, or silent swallowing of security-critical errors.

**CWE:** CWE-209, CWE-248, CWE-396, CWE-397, CWE-755, CWE-703

---

## Positive Indicators (classify as Exceptional Conditions Failure)

Classify when error handling matches ANY:

| Pattern | Code-level indicators |
|---------|----------------------|
| **Stack trace to client** | `traceback.format_exc()` in HTTP response, Flask debug page in prod, `e.printStackTrace()` to servlet output |
| **SQL/details in error message** | `return str(e)` where exception contains query/schema info shown to user |
| **Fail-open on auth error** | `except AuthError: pass` or `return True` — request proceeds without auth |
| **Empty catch swallows security failure** | `catch (Exception e) {}` around auth, crypto, or validation block |
| **Generic catch returns success** | Catch block returns 200 OK after security check failure |
| **Log-and-continue on integrity failure** | Signature verification fails but processing continues |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Exceptional Conditions Failure when:

| Condition | Why excluded |
|-----------|--------------|
| Generic user message returned; details logged server-side only | Correct handling |
| Auth failure returns 401/403 and aborts request | Fail-closed |
| Catch block re-throws or returns error after security check | Proper propagation |
| Detailed errors behind admin-only diagnostic endpoint with auth | Scoped disclosure |
| Expected business exceptions (not found, validation) with safe messages | Normal flow |

---

## Investigation — Research Agent

1. `grep -rn` for `except`, `catch`, `traceback`, `printStackTrace`, `format_exc`
2. `read` catch blocks on auth, payment, crypto paths
3. Confirm error content reaches client or fails open

**Done when:** Each error-handling flaw has hypothesis + file:line.

---

## Planning — Planner Agent

1. One step block per error-handling hypothesis
2. EDIT replaces verbose/fail-open catch with fail-closed + generic message
3. Preserve server-side logging in fix

**Done when:** All exception-handling hypotheses addressed.

---

## Implementation — Executor Agent

1. Single batched Cursor call
2. VERIFY: no stack trace/SQL in response paths; auth catch fails closed

**Done when:** Error responses are generic; security invariants preserved.

---

## Recommended Fixes

- Fail closed on auth/crypto errors
- Return generic user messages; log details server-side
- Never swallow exceptions in security-critical blocks

---

## Validation Strategy

- Trigger error — response must not contain stack trace or SQL
- Auth failure must not grant access
