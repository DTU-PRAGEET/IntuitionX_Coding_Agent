---
name: authentication-failures
module: security
description: Classify and remediate Authentication Failures — weak session, broken MFA, JWT issues, credential stuffing gaps
agents: [research, planner, implement]
trigger_labels: authentication-failures, broken-auth, session, jwt, mfa, credential-stuffing, oauth
---

# Authentication Failures (OWASP A07)

## Definition

The application fails to verify identity correctly — weak credentials, broken session management, or flawed token validation allows impersonation.

**CWE:** CWE-287, CWE-297, CWE-384, CWE-613, CWE-620, CWE-798

---

## Positive Indicators (classify as Authentication Failure)

Classify when the code **implements or configures authentication** and matches ANY:

| Pattern | Code-level indicators |
|---------|----------------------|
| **Weak JWT validation** | `verify=False`, `algorithms=["none"]`, missing signature check, accepting `alg: none` |
| **Session fixation / weak session** | Session ID not regenerated after login; predictable session tokens |
| **Missing logout invalidation** | Logout clears client cookie only; server session remains valid |
| **Credentials in URL/logs** | Password or token in query string, logged in plaintext |
| **No lockout/rate limit on login** | Unlimited password attempts with no delay or lockout |
| **Hardcoded test credentials in prod path** | `if password == "admin"` or default creds in auth flow |
| **Broken password storage** | Plaintext passwords, MD5/SHA1 without salt for password hashing |
| **Missing MFA bypass** | MFA check skipped when specific header/param present |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Authentication Failure when:≠≠≠≠≠

| Condition | Why excluded |
|-----------|--------------|
| bcrypt/Argon2/scrypt used for password hashing | Strong storage |
| JWT verified with explicit algorithm allowlist and secret/key | Correct validation |
| Session cookie has `HttpOnly`, `Secure`, `SameSite` in production config | Hardened session (may still be other issues) |
| OAuth handled by framework with standard token validation | Delegated correctly |
| 401 on missing credentials on protected route | Expected behavior |
| Internal mTLS service token on private network with documented design | Architecture decision, not broken auth |

---

## Investigation — Research Agent

1. `grep -rn` for `jwt`, `session`, `login`, `password`, `authenticate`
2. `read` auth middleware, login handlers, session config
3. Confirm indicator matches runtime auth path (not test-only code)

**Done when:** Each auth weakness has hypothesis with file:line evidence.

---

## Planning — Planner Agent

1. One step block per auth hypothesis
2. Fix session/JWT/password handling with real EDIT patches
3. Config-only fixes → EDIT config file with distinct old/new strings

**Done when:** All auth hypotheses addressed; no identical old/new EDIT steps.

---

## Implementation — Executor Agent

1. Batch all auth fixes in one Cursor call
2. VERIFY: weak pattern removed (`alg=none`, plaintext password compare, etc.)

**Done when:** Auth tests pass; VERIFY patterns satisfied.

---

## Recommended Fixes

- Secure session cookies; regenerate session on login
- Validate JWT with algorithm allowlist; reject `none`
- bcrypt/Argon2 for passwords; rate-limit login
- Invalidate server session on logout

---

## Validation Strategy

- Test login/logout lifecycle
- Attempt JWT with `alg:none` — must reject
- VERIFY forbidden auth patterns absent
