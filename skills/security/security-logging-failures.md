---
name: security-logging-failures
module: security
description: Classify and remediate Security Logging Failures — missing audit trails, log injection, sensitive data in logs
agents: [research, planner, implement]
trigger_labels: security-logging, logging-failure, audit, monitoring, log-injection, insufficient-logging
---

# Security Logging and Alerting Failures (OWASP A09)

## Definition

Security events are not logged, alerts are missing, or logs expose/inject data — preventing detection, forensics, or enabling log-based attacks.

**CWE:** CWE-117, CWE-223, CWE-532, CWE-778, CWE-779

---

## Positive Indicators (classify as Security Logging Failure)

Classify when logging around security events matches ANY:

| Pattern | Code-level indicators |
|---------|----------------------|
| **No auth failure logging** | Login/register handler returns 401 with zero log statement |
| **Sensitive data in logs** | `log.info("password=" + password)`, logging full JWT/session tokens, PAN/SSN in log lines |
| **Log injection** | `logger.info("User: " + userInput)` where userInput contains `\n` fake log entries |
| **Missing audit on sensitive action** | Admin delete/export/permission change with no audit log entry |
| **Swallowed security events** | Catch block logs at DEBUG only or not at all for authz failures |
| **No correlation ID** | Security-sensitive distributed flow with no request ID in logs |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Security Logging Failure when:

| Condition | Why excluded |
|-----------|--------------|
| Auth failures logged at WARN with user ID (not password) | Adequate logging |
| PII masked/redacted before logging (`maskPassword()`) | Protected logging |
| Structured audit log entry on admin actions | Audit present |
| Verbose debug logs gated to dev profile only | Not production issue |
| Centralized logging at gateway captures auth failures app omits | Compensating control |

---

## Investigation — Research Agent

1. `grep -rn` for `log.`, `logger.`, `LOG.`, `audit` in auth/admin handlers
2. `read` login, logout, permission-change code paths
3. Confirm missing log or sensitive data in log format string

**Done when:** Each logging gap has hypothesis + handler evidence.

---

## Planning — Planner Agent

1. One step block per logging hypothesis
2. EDIT adds structured audit log or redacts sensitive fields
3. Use parameterized logging to prevent injection

**Done when:** All logging hypotheses have fix steps.

---

## Implementation — Executor Agent

1. Single batched Cursor call
2. VERIFY: audit log present; password/token not in log statements

**Done when:** Security events produce appropriate log entries.

---

## Recommended Fixes

- Log authn/authz failures at WARN/ERROR with correlation ID
- Redact secrets; use parameterized logging
- Audit trail for admin and data-export actions

---

## Validation Strategy

- Trigger auth failure — log entry must appear
- Grep confirms no password/token in log format strings
