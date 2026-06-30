---
name: broken-access-control
module: security
description: Classify and remediate Broken Access Control — IDOR, BOLA, missing authorization, privilege escalation
agents: [research, planner, implement]
trigger_labels: broken-access-control, idor, bola, access-control, authorization, privilege-escalation
---

# Broken Access Control (OWASP A01)

## Definition

The application fails to enforce authorization — users act outside intended permissions, access objects they do not own, or escalate privileges.

**CWE:** CWE-284, CWE-285, CWE-639, CWE-862, CWE-863

---

## Positive Indicators (classify as Broken Access Control)

Classify when **all** of the following hold:

1. An authenticated or unauthenticated user can access a **protected resource or action**
2. No server-side authorization check ties the request to the authenticated principal

| Pattern | Code-level indicators (match ANY) |
|---------|-------------------------------------|
| **IDOR / BOLA** | Handler loads resource by ID from URL/body with no ownership check: `getById(request.getParameter("id"))` |
| **Missing role guard** | Admin/sensitive endpoint with no `@PreAuthorize`, `@RolesAllowed`, or equivalent role check |
| **Client-side auth only** | UI hides button but server endpoint accepts request without role validation |
| **Path traversal as access bypass** | `../` in path reaches files outside intended directory with no ACL |
| **Forced browsing** | DELETE/PUT on `/api/users/{id}` without verifying caller owns `id` |
| **Privilege escalation** | User can set `role=admin` or `isAdmin=true` in request body persisted to DB |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Broken Access Control when:

| Condition | Why excluded |
|-----------|--------------|
| `@PreAuthorize` / middleware checks role before handler logic | Authorization enforced |
| Resource loaded via `findByIdAndOwner(id, currentUser)` | Ownership verified |
| Endpoint is intentionally public and documented (health check, static assets) | By design |
| 401 returned when unauthenticated | Authentication failure, not access control |
| User accesses only their own data via session-bound query | Correct enforcement |
| Admin-only feature with server-side role check on every request | Control present |

---

## Investigation — Research Agent

1. `read` route handlers for endpoints named in ticket
2. `grep -rn` for `@PreAuthorize`, `authorize`, `checkPermission`, `currentUser`
3. Trace: request param ID → DB lookup → response; confirm missing ownership check
4. Emit one hypothesis per endpoint missing authorization

**Done when:** Each access-control gap has hypothesis + handler file evidence.

---

## Planning — Planner Agent

1. One step block per authorization gap hypothesis
2. EDIT adds server-side check before resource access
3. NEVER skip a hypothesis from Research Report

**Done when:** Every access-control hypothesis has READ→EDIT→VERIFY or documented mitigation.

---

## Implementation — Executor Agent

1. Single batched Cursor call for all authorization fixes
2. VERIFY: role/ownership check present before sensitive operation

**Done when:** Unauthorized path returns 403/404 in tests or VERIFY patterns.

---

## Recommended Fixes

- Enforce authorization on every object access server-side
- Bind queries to authenticated principal
- Deny by default; explicit allow per role

---

## Validation Strategy

- Test authorized path succeeds; unauthorized path blocked
- VERIFY grep for auth check before DB read/write on sensitive endpoints
