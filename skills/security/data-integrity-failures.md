---
name: data-integrity-failures
module: security
description: Classify and remediate Data Integrity Failures — insecure deserialization, unsigned updates, CI/CD tampering
agents: [research, planner, implement]
trigger_labels: data-integrity, deserialization, integrity-failure, unsigned-update, objectinputstream
---

# Software and Data Integrity Failures (OWASP A08)

## Definition

Code or data integrity is not verified — untrusted deserialization, unsigned artifacts, or tampered updates execute or load malicious content.

**CWE:** CWE-345, CWE-353, CWE-426, CWE-494, CWE-502, CWE-829

---

## Positive Indicators (classify as Data Integrity Failure)

Classify when untrusted input is deserialized or loaded without integrity verification:

| Pattern | Code-level indicators |
|---------|----------------------|
| **Insecure deserialization** | `ObjectInputStream.readObject()` on request data, `pickle.loads(user_bytes)`, `yaml.load()` without SafeLoader |
| **Unsigned updates** | Auto-update downloads and executes binary without signature check |
| **Missing artifact verification** | CI pulls dependencies/scripts without checksum or signature validation |
| **JWT without integrity** | Custom token parsed without HMAC/signature verification |
| **Eval of remote content** | `eval()`, `exec()` on downloaded or user-supplied code |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Data Integrity Failure when:

| Condition | Why excluded |
|-----------|--------------|
| `json.loads()` on structured API input with schema validation | JSON is not executable deserialization |
| `yaml.safe_load()` or SafeLoader used | Safe parsing |
| Deserialization of internal cache with HMAC verified at transport | Integrity enforced |
| Signed JAR/plugins with signature verified before load | Integrity checked |
| `ObjectInputStream` with strict allowlist filter (JEP 290) | Mitigation present |

---

## Investigation — Research Agent

1. `grep -rn` for `readObject`, `pickle`, `yaml.load`, `marshal`, `eval(`
2. `read` deserialization call sites — trace input source
3. Confirm input is attacker-controllable (HTTP, message queue, file upload)

**Done when:** Each deserialization/integrity gap has hypothesis + sink evidence.

---

## Planning — Planner Agent

1. One step block per integrity hypothesis
2. Replace unsafe deserialization or add signature verification
3. Real EDIT required — never no-op patches

**Done when:** All integrity hypotheses have fix steps.

---

## Implementation — Executor Agent

1. Single batched Cursor call
2. VERIFY: unsafe deserializer removed or allowlist filter added

**Done when:** Untrusted deserialization path eliminated.

---

## Recommended Fixes

- Avoid deserializing untrusted data; use JSON with schema validation
- Sign artifacts; verify before load/update
- Use `yaml.safe_load`, pickle only on trusted internal data

---

## Validation Strategy

- Attempt malicious serialized payload — must fail safely
- VERIFY grep confirms unsafe API removed
