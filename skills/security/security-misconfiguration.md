---
name: security-misconfiguration
module: security
description: Classify and remediate Security Misconfiguration — insecure defaults, debug in prod, missing headers, open CORS
agents: [research, planner, implement]
trigger_labels: security-misconfiguration, misconfiguration, default-credentials, cors, security-headers, debug-enabled
---

# Security Misconfiguration (OWASP A05)

## Definition

Security settings, defaults, or deployment configuration expose the application — debug modes, permissive CORS, missing headers, or unchanged default credentials.

**CWE:** CWE-16, CWE-2, CWE-611, CWE-1188, CWE-1004

---

## Positive Indicators (classify as Security Misconfiguration)

Classify when configuration or deployment code matches ANY:

| Pattern | Code-level indicators |
|---------|----------------------|
| **Debug in production** | `debug=True`, `DEBUG=1`, `spring.profiles.active` includes dev features in prod path |
| **Permissive CORS** | `Access-Control-Allow-Origin: *` with credentials, or CORS allows all origins on sensitive API |
| **Missing security headers** | No `Content-Security-Policy`, `X-Frame-Options`, `Strict-Transport-Security` on web app |
| **Default credentials** | `admin/admin`, factory default passwords in config committed to repo |
| **Directory listing enabled** | `autoindex on`, static file server lists directories |
| **Verbose stack traces to users** | `app.debug=True` with full tracebacks in HTTP responses |
| **Insecure cookie defaults** | Session cookie without `HttpOnly` or `Secure` in production config |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Security Misconfiguration when:

| Condition | Why excluded |
|-----------|--------------|
| Debug enabled only when `ENV=development` with prod profile separate | Correctly gated |
| CORS restricted to explicit allowlist of origins | Properly configured |
| Security headers set at reverse proxy/ingress (not app code) | Enforced elsewhere |
| Default creds in docker-compose for local dev only, not prod deploy | Dev-only |
| Health endpoint intentionally unauthenticated | Documented design |

---

## Investigation — Research Agent

1. `read` app init, config YAML/properties, middleware setup
2. `grep -rn` for `debug`, `CORS`, `allow_origin`, `csrf`, cookie settings
3. Confirm config applies to production code path

**Done when:** Each misconfiguration has hypothesis + config file evidence.

---

## Planning — Planner Agent

1. One step block per misconfiguration hypothesis
2. EDIT changes config to secure defaults
3. Verify-only if fix applied in shared config elsewhere

**Done when:** All config issues addressed per hypothesis.

---

## Implementation — Executor Agent

1. Single batched Cursor call for config changes
2. VERIFY: insecure default absent (`debug=True`, `Allow-Origin: *`, etc.)

**Done when:** Secure configuration confirmed in VERIFY.

---

## Recommended Fixes

- Disable debug in production; restrict CORS
- Add security headers; remove default accounts
- Harden cookie and session settings

---

## Validation Strategy

- VERIFY grep for insecure config values removed
- Confirm prod profile does not load dev settings
