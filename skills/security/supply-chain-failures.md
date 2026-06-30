---
name: supply-chain-failures
module: security
description: Classify and remediate Supply Chain Failures — vulnerable dependencies, typosquatting, unsigned artifacts
agents: [research, planner, implement]
trigger_labels: supply-chain, sca, dependency, cve, outdated-component, vulnerable-component
---

# Software Supply Chain Failures (OWASP A06)

## Definition

The application depends on vulnerable, tampered, or untrusted third-party components — outdated libraries with known CVEs, unpinned deps, or unverified packages.

**CWE:** CWE-1104, CWE-937, CWE-829, CWE-494

---

## Positive Indicators (classify as Supply Chain Failure)

Classify when dependency or build configuration matches ANY:

| Pattern | Code-level indicators |
|---------|----------------------|
| **Known vulnerable version** | `pom.xml`/`package.json`/`requirements.txt` pins version with published CVE affecting reachable code |
| **Unpinned dependencies** | Floating versions (`*`, `LATEST`, no lockfile) in production build |
| **SNAPSHOT in production** | `-SNAPSHOT` dependency in prod profile or release build |
| **Typosquat risk** | Package name one char off known library (`reqeusts` vs `requests`) |
| **Unsigned/unverified plugin** | Build pulls executable plugin without checksum verification |
| **Abandoned EOL library** | Dependency with no security patches, used for security-sensitive function |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Supply Chain Failure when:

| Condition | Why excluded |
|-----------|--------------|
| CVE exists but vulnerable function not reachable from app code | Not exploitable in this context — flag `cve_not_reachable` |
| Vulnerability in devDependency only (test/build tools) | Not in production artifact |
| Dependency already at patched minimum version per advisory | Already remediated |
| Internal/private artifact from trusted registry with signing | Verified supply chain |
| Scanner false positive on wrong component version | Version mismatch in report |

---

## Investigation — Research Agent

1. `read` manifest files: `pom.xml`, `build.gradle`, `package.json`, `requirements.txt`, `go.mod`
2. Match ticket CVE/advisory to pinned version
3. `grep -rn` for imports of vulnerable API in application code — confirm reachability

**Done when:** Each vulnerable dependency has hypothesis with version + reachability evidence.

---

## Planning — Planner Agent

1. One step block per dependency/CVE hypothesis
2. EDIT upgrades version in manifest; add lockfile pin if missing
3. Include RUN_TESTS if dependency upgrade affects build

**Done when:** All supply-chain hypotheses have upgrade steps.

---

## Implementation — Executor Agent

1. Single batched Cursor call for manifest updates
2. VERIFY: vulnerable version string absent; upgraded version present

**Done when:** Dependency audit passes for cited CVE.

---

## Recommended Fixes

- Upgrade to patched version per advisory
- Pin versions; use lockfiles; run dependency audit in CI
- Replace abandoned libraries

---

## Validation Strategy

- Re-run SCA scanner on updated manifest
- Build and test pass after upgrade
