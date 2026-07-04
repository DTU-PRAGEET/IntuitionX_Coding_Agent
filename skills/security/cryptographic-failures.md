---
name: cryptographic-failures
module: security
description: Classify and remediate Cryptographic Failures — weak algorithms, hardcoded secrets, cleartext sensitive data
agents: [research, planner, implement]
trigger_labels: cryptographic-failures, weak-crypto, hardcoded-secret, hardcoded-credentials, secrets, committed-secret, committed-secrets, sensitive-data, encryption, tls
---

# Cryptographic Failures (OWASP A02)

## Definition

Sensitive data lacks adequate protection in transit or at rest — weak crypto, exposed secrets, or cleartext storage of confidential information.

**CWE:** CWE-259, CWE-327, CWE-331, CWE-798, CWE-321

---

## Positive Indicators (classify as Cryptographic Failure)

Classify when sensitive data or crypto operations match ANY:

| Pattern | Code-level indicators |
|---------|----------------------|
| **Hardcoded secrets** | `API_KEY = "..."`, `password = "..."`, `secret_key = '...'` in source (not env/vault) |
| **Weak password hashing** | `MD5(password)`, `SHA1(password)`, unsalted hash for credentials |
| **Weak encryption** | DES, RC4, ECB mode, static/zero IV, `AES/ECB/PKCS5Padding` |
| **Cleartext sensitive data** | SSN, PAN, password stored/transmitted without encryption |
| **HTTP for sensitive auth** | Login/token over `http://` without TLS redirect in production config |
| **Insecure random** | `Math.random()` or `random.random()` for tokens/session IDs |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Cryptographic Failure when:

| Condition | Why excluded |
|-----------|--------------|
| MD5/SHA1 used for non-security checksums (file integrity, cache keys) | Not protecting confidentiality |
| Secrets loaded from environment variables or secret manager | Not hardcoded |
| bcrypt/Argon2/scrypt for passwords | Adequate hashing |
| AES-GCM with random IV per encryption | Strong encryption |
| Test fixtures with dummy keys in `src/test/` only | Test scope |
| TLS 1.2+ enforced in production ingress/config | Transit protected |

---

## Investigation — Research Agent

1. `grep -rn` for `password`, `secret`, `key=`, `MD5`, `SHA1`, `AES`, `encrypt`
2. `read` config and crypto utility files
3. Distinguish test fixtures from production paths

**Done when:** Each crypto weakness has hypothesis with evidence.

---

## Planning — Planner Agent

1. One step block per crypto hypothesis
2. Replace weak crypto with framework-native secure APIs
3. Move hardcoded secrets to env/config references

**Done when:** All hypotheses mapped; EDIT patches change crypto/secrets.

---

## Implementation — Executor Agent

1. Single batched Cursor call
2. VERIFY: weak algorithm and hardcoded secret patterns removed

**Done when:** VERIFY confirms secure replacements.

---

## Recommended Fixes

- bcrypt/Argon2 for passwords; AES-GCM with random IV
- KMS/vault for secrets; TLS 1.2+ for transit
- Remove secrets from source control

---

## Validation Strategy

- Grep confirms no hardcoded secrets in changed files
- Dependency scan for crypto libraries if applicable
