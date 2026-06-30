# Security Skills Module

Team-specific security remediation skills aligned with OWASP Top 10 categories.

## Skills

### OWASP category skills (auto-selected by `vulnerability_category` or labels)

- **broken-access-control.md** — IDOR, BOLA, missing authorization (CWE-284/285/639/862)
- **security-misconfiguration.md** — default credentials, debug mode, insecure headers (CWE-16/2/611)
- **supply-chain-failures.md** — vulnerable/outdated dependencies, SCA/CVE (CWE-1104/937)
- **cryptographic-failures.md** — weak crypto, hardcoded secrets, cleartext data (CWE-259/327/798)
- **injection.md** — SQLi, XSS, command/LDAP injection (CWE-89/79/77/78)
- **insecure-design.md** — missing threat controls, trust boundary violations (CWE-209/256/501)
- **authentication-failures.md** — broken auth, session/JWT issues (CWE-287/297/384/620)
- **data-integrity-failures.md** — insecure deserialization, unsigned updates (CWE-345/502/494)
- **security-logging-failures.md** — insufficient audit/logging, log injection (CWE-117/532/778)
- **exceptional-conditions.md** — verbose errors, fail-open on exceptions (CWE-209/248/396/755)

### Java-specific skills

- **java-sast-fixes.md** — Static analysis fixes (SQLi, XSS, path traversal)
- **java-sca-fixes.md** — Dependency/CVE remediation for Java projects

## Usage

Skills activate when ticket `vulnerability_category` or labels match `trigger_labels`. Research, Planner, and Executor agents receive matched domain skills via `SkillRegistry.select()`.

## Adding a skill

Place a new `.md` file in this directory with YAML frontmatter (`name`, `module: security`, `agents`, `trigger_labels`) and sections: `prompt_templates`, `implementation_guidance`, `completion_criteria`. No code changes required — auto-discovered on restart.
