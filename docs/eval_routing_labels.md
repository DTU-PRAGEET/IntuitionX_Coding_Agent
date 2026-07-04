# Routing Labels for the Golden Set (derived)

How the `expected_route` for each golden case is decided, so the `route_correct`
evaluator scores against the orchestrator's **actual** decision logic, not a guess.

## The rule that dominates everything

From `skills/orchestrator/orchestrator.md`:

- **Step 2 — EXIT** if: status is Done/Resolved/Closed, empty/invalid request,
  token budget ≥80% consumed, or out-of-scope (not software engineering).
- **Step 3 — Auth/security override:** if `labels` or `module` contain any of
  `auth`, `security`, `cve`, `sqli`, `vulnerability` → `risk_level=HIGH` and
  **never route MICRO**.
- **Step 4 — FULL** if any: complex/ambiguous, prior retry, auth/security labels,
  repo unresolved, missing acceptance criteria.
- **SKIP_RESEARCH** only if ALL: narrow single-file scope, clear requirements,
  no prior attempt, repo resolved, **not** auth/security.
- **MICRO** only if ALL: 1–2 files explicitly named, **no** auth/security/refactor
  keywords, trivial single-line/config change, no tests implied.

**Consequence:** a security finding that arrives *labelled as security* is forced
to `FULL`. The only way a vuln case is `MICRO`/`SKIP_RESEARCH` is if it is framed
as a plain hardening/config change (no security keywords) that names the exact
file(s). We use that deliberately to get route diversity.

## Per-repo routing tendency

| Repo | Typical vuln framing | Dominant route | Why |
|------|----------------------|----------------|-----|
| Testing-Security-demo (DVPWA) | security-labelled SQLi/XSS/MD5 | FULL | security override |
| VAmPI | API security (BOLA/JWT/SQLi) | FULL | security override |
| WebGoat | lesson-based, large repo | FULL | size + security |
| Juice Shop | realistic large app | FULL | size + security |
| DVWA | per-level vuln modules | FULL | security override |
| WrongSecrets | hardcoded secrets | FULL or SKIP_RESEARCH | "secrets" is **not** an override keyword → SKIP if file named |
| leaky-repo | secrets in config files | MICRO / SKIP_RESEARCH | named artifact files, no app logic |
| CentralBank / vuln-spring | banking IDOR/SQLi | FULL | security override |
| NodeGoat / RailsGoat / JavaSecLab / DVGA | broad OWASP | FULL | security override |
| (any) hardening/config item, no security label | "set debug=False at file:line" | MICRO / SKIP_RESEARCH | no override keyword + named file |

## Golden 20 — assigned routes

| ID | Case | Repo | Vuln class | Difficulty | Labels (framing) | Route | Rationale |
|----|------|------|-----------|-----------|------------------|-------|-----------|
| G-01 | SQLi in student create | DVPWA | Injection | easy | security, sqli | **FULL** | security override |
| G-02 | MD5 password hashing | DVPWA | Secrets/Crypto | easy | security, crypto | **FULL** | security override |
| G-03 | Debug mode + verbose errors (config hardening) | DVPWA | Misconfig | easy | misconfiguration, hardening | **SKIP_RESEARCH** | 2 named files, clear, no security keyword |
| G-04 | Session cookie not HttpOnly | DVPWA | Misconfig | medium | hardening | **MICRO** | single file+line named, trivial flag change |
| G-05 | Stored XSS in reviews | DVPWA | XSS | medium | security, xss | **FULL** | security override |
| G-06 | BOLA — book secrets | VAmPI | IDOR | easy | security, idor | **FULL** | security override |
| G-07 | SQLi via API param | VAmPI | Injection | medium | security, sqli | **FULL** | security override |
| G-08 | Weak JWT secret | VAmPI | Auth | medium | security, auth | **FULL** | security override |
| G-09 | Flask debug mode | VAmPI | Misconfig | easy | misconfiguration | **MICRO** | `app.py` named, single flag |
| G-10 | SQLi lesson 2 | WebGoat | Injection | medium | security, sqli | **FULL** | security + large repo |
| G-11 | JWT alg/secret bypass | WebGoat | Auth | hard | security, auth | **FULL** | security override |
| G-12 | IDOR lesson | WebGoat | IDOR | hard | security | **FULL** | security override |
| G-13 | Command injection | DVWA | SAST | medium | security | **FULL** | security override |
| G-14 | DOM XSS | Juice Shop | XSS | medium | security, xss | **FULL** | security override |
| G-15 | Admin section forced browsing | Juice Shop | IDOR/BAC | medium | security, access-control | **FULL** | security override |
| G-16 | Weak JWT secret `"secret"` | Juice Shop | Auth | easy | security, auth | **FULL** | security override |
| G-17 | Hardcoded password in source | WrongSecrets | Secrets | easy | secrets | **SKIP_RESEARCH** | 2 named files, "secrets" not an override keyword |
| G-18 | Secret in application.properties | WrongSecrets | Secrets | medium | secrets, config | **MICRO** | single named config file |
| G-19 | Already-fixed SQLi (status Done) | — | Routing | easy | security, sqli (status=Done) | **EXIT** | Step 2: status Done |
| G-20 | "Add dark mode to dashboard" | — | Routing | easy | feature | **EXIT** | Step 2: out of scope |

**Distribution:** FULL ×13, SKIP_RESEARCH ×2, MICRO ×3, EXIT ×2.

> Note: G-00 (the harness dummy) is a status=Done security ticket → **EXIT**; it
> exists only to prove the offline scorer + report work.

## Authoring guidance baked into this

- To make a case `FULL`: include a security keyword in `labels` or `module`.
- To make a case `MICRO`: drop all security keywords, name exactly 1–2 files
  (ideally with the line), keep the change trivial (flag/config/single line).
- To make a case `SKIP_RESEARCH`: like MICRO but 1–2 files of narrow scope where
  some reasoning is needed (not strictly a one-liner), still no security keyword.
- To make a case `EXIT`: set `status: "Done"` (or `Resolved`/`Closed`), or frame a
  non-engineering / feature request.





# Offline gate check (uses G-00/G-01 fixtures only)
make evals-gate

# Live unit evals (need LLM; implement/e2e also need Cursor)
make evals-routing
make evals-research
make evals-plan
make evals-implement
make evals              # full e2e, real patches

# Or via unified CLI
python -m evals.runners.run_eval --mode research --cases evals/golden/G-01-dvpwa-sqli.json
python -m evals.runners.run_research_eval --gate   # live + gate check