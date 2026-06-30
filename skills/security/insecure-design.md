---
name: insecure-design
module: security
description: Classify and remediate Insecure Design — missing threat controls, trust boundary violations, unsafe workflows
agents: [research, planner, implement]
trigger_labels: insecure-design, design-flaw, threat-model, missing-control, trust-boundary
---

# Insecure Design (OWASP A04)

## Definition

The application architecture or workflow lacks security controls at design level — not a single coding bug, but a missing control that enables abuse (rate limits, step-up auth, server-side validation).

**CWE:** CWE-209, CWE-256, CWE-501, CWE-522, CWE-841

---

## Positive Indicators (classify as Insecure Design)

Classify when the **workflow or architecture** lacks a required control and matches ANY:

| Pattern | Code-level indicators |
|---------|----------------------|
| **Client-only validation** | Sensitive action proceeds with only JS/HTML `required`/`pattern` — no server validation |
| **Security decision in UI only** | Admin features gated by frontend flag; backend accepts request regardless |
| **Missing rate limit on sensitive flow** | Password reset, OTP, coupon redemption with unlimited requests |
| **No step-up for high-risk action** | Wire transfer / delete-all without re-authentication |
| **Trust boundary violation** | Server trusts client-sent price, role, or user ID without verification |
| **Missing anti-automation** | Login/register with no CAPTCHA or rate limit under brute-force conditions |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Insecure Design when:

| Condition | Why excluded |
|-----------|--------------|
| Server validates all inputs with schema/validator before processing | Control exists |
| Rate limiting implemented in middleware or gateway | Control exists elsewhere |
| UI validation duplicated with identical server-side checks | Defense in depth present |
| Single coding bug (missing escape, missing auth on one line) | Implementation flaw — use Injection or Access Control |
| Feature is internal-only with network-level access control | Compensating control documented |

---

## Investigation — Research Agent

1. Map workflow: entry point → validation → action
2. `read` handler — confirm server-side control absent
3. Distinguish design gap from single-line implementation bug

**Done when:** Each missing control has hypothesis describing workflow gap.

---

## Planning — Planner Agent

1. One step block per design-gap hypothesis
2. ADD server-side control (validator, rate limit, step-up check)
3. May require new middleware or handler logic — real EDIT required

**Done when:** Every design hypothesis has implementation steps.

---

## Implementation — Executor Agent

1. Batch structural control additions in one Cursor call
2. VERIFY: server-side check exists before sensitive action

**Done when:** Abuse scenario blocked by new control.

---

## Recommended Fixes

- Add server-side validation for all sensitive inputs
- Rate limiting on auth and high-value flows
- Step-up authentication for destructive operations

---

## Validation Strategy

- Test bypass of client validation — server must reject
- Test rate limit triggers on repeated sensitive requests
