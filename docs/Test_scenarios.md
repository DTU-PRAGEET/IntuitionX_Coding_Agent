Great timing to think about this systematically. Let me give you the full picture — both the LangSmith mechanics and a complete scenario taxonomy that a senior test engineer would design.

---

## Part 1: LangSmith Dataset — How to Build It

### Core Concepts First

A LangSmith dataset is just a collection of **input/output pairs** (called "examples"). When you run an eval, LangSmith feeds each input to your pipeline, compares the output to your expected output using **evaluator functions** you define, and gives you a scorecard.

For your pipeline, each example looks like:

```
Input  → Jira ticket + repo context
Output → What the agent SHOULD do (route, files, fix type, patch quality)
```

### Step 1: Install & Authenticate

```bash
pip install langsmith
export LANGCHAIN_API_KEY="ls__your_key"
export LANGCHAIN_TRACING_V2=true
export LANGCHAIN_PROJECT="IntuitionX"
```

### Step 2: Define Your Dataset Schema

Before creating anything, lock down what an "example" looks like. I'd recommend this schema for your use case:

```python
# schemas.py

from typing import TypedDict, List, Optional, Literal

class JiraTicket(TypedDict):
    ticket_id: str
    summary: str
    description: str
    severity: Optional[str]          # CVSS score or label
    reporter: Optional[str]
    labels: Optional[List[str]]
    repo_url: Optional[str]
    branch: Optional[str]
    affected_files: Optional[List[str]]  # sometimes ticket mentions files

class DatasetInput(TypedDict):
    jira_ticket: JiraTicket
    pipeline_mode_hint: Optional[str]    # only for debugging; agent should decide

class DatasetOutput(TypedDict):
    expected_route: Literal["FULL", "MICRO", "SKIP_RESEARCH", "EXIT"]
    expected_files_identified: List[str]         # which files research should find
    expected_fix_type: str                       # e.g. "PARAMETERIZE_QUERY", "HASH_WITH_BCRYPT"
    expected_vuln_class: str                     # e.g. "SQL_INJECTION", "BROKEN_ACCESS_CONTROL"
    expected_patch_contains: List[str]           # substrings/patterns the patch MUST include
    expected_patch_excludes: List[str]           # patterns the patch must NOT include
    difficulty: Literal["easy", "medium", "hard"]
    owasp_category: str                          # e.g. "A05:2025"
    pass_criteria: List[str]                     # plain-English statements for LLM judge
```

### Step 3: Create the Dataset Programmatically

```python
from langsmith import Client

client = Client()

# Create the dataset once
dataset = client.create_dataset(
    dataset_name="IntuitionX-Golden-Set-v1",
    description="40 security bug cases covering OWASP Top 10:2025 for baseline eval"
)

# Add examples one by one (or in bulk)
examples = [
    {
        "inputs": {
            "jira_ticket": {
                "ticket_id": "SEC-001",
                "summary": "SQL Injection in user search endpoint",
                "description": "The /api/users/search endpoint builds SQL queries via string concatenation. Attacker can dump entire users table via ' OR '1'='1",
                "severity": "9.1",
                "repo_url": "https://github.com/your-org/dvpwa",
                "branch": "main",
                "affected_files": None  # deliberately empty; research agent must find it
            }
        },
        "outputs": {
            "expected_route": "FULL",
            "expected_files_identified": ["src/api/users/search.py"],
            "expected_fix_type": "PARAMETERIZE_QUERY",
            "expected_vuln_class": "SQL_INJECTION",
            "expected_patch_contains": ["?", "prepared statement", "parameterized"],
            "expected_patch_excludes": ["f\"", "format(", "+ query"],
            "difficulty": "easy",
            "owasp_category": "A05:2025",
            "pass_criteria": [
                "Research agent identifies the correct file",
                "Fix uses parameterized queries, not sanitization",
                "No string concatenation in the patched SQL call"
            ]
        }
    },
    # ... more examples
]

client.create_examples(
    inputs=[e["inputs"] for e in examples],
    outputs=[e["outputs"] for e in examples],
    dataset_id=dataset.id
)
```

### Step 4: Write Your Evaluators

This is the most important part. Each evaluator is a function that scores one aspect of the output:

```python
from langsmith.schemas import Run, Example
from langsmith.evaluation import evaluate

# Evaluator 1: Did research find the right file?
def file_identification_accuracy(run: Run, example: Example) -> dict:
    expected = set(example.outputs["expected_files_identified"])
    actual = set(run.outputs.get("research_output", {}).get("files", []))
    
    if not expected:
        return {"key": "file_accuracy", "score": 1.0}
    
    precision = len(expected & actual) / len(actual) if actual else 0
    recall = len(expected & actual) / len(expected)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0
    
    return {"key": "file_identification_f1", "score": f1}


# Evaluator 2: Did the orchestrator pick the right route?
def route_correctness(run: Run, example: Example) -> dict:
    expected = example.outputs["expected_route"]
    actual = run.outputs.get("route_taken")
    return {
        "key": "route_correct",
        "score": 1 if actual == expected else 0
    }


# Evaluator 3: Patch quality (LLM-as-judge for semantic correctness)
from langsmith.evaluation import LangChainStringEvaluator

patch_judge = LangChainStringEvaluator(
    "criteria",
    config={
        "criteria": {
            "fixes_vulnerability": "Does the patch actually fix the vulnerability described?",
            "no_new_bugs": "Does the patch avoid introducing new obvious bugs?",
            "minimal_change": "Is the change minimal and surgical, not a full rewrite?"
        }
    }
)


# Run all evaluators against the dataset
results = evaluate(
    lambda inputs: your_pipeline.run(inputs["jira_ticket"]),
    data="IntuitionX-Golden-Set-v1",
    evaluators=[
        file_identification_accuracy,
        route_correctness,
        patch_judge
    ],
    experiment_prefix="week2-baseline",
    max_concurrency=2   # run 2 cases in parallel; careful with rate limits
)
```

---

## Part 2: Complete Test Scenario Taxonomy

Here is the full scenario map. I've extended what you started and organized by layer.

---

### Layer 1 — Orchestrator Routing Decisions

These test that the orchestrator picks the right pipeline *before* any agent does real work.

| ID | Scenario | Input Signal | Expected Route | Why It's Important |
|----|----------|-------------|----------------|-------------------|
| R-01 | Clear high-severity vuln with repo URL | CVSS 9.1, full description, repo linked | FULL | Happy path |
| R-02 | Tiny isolated vuln (1 function, obvious fix) | "Change MD5 to bcrypt in PasswordUtil.java line 42" | MICRO / SKIP_RESEARCH | Avoids wasting research budget |
| R-03 | Ticket already marked DONE in Jira | status=DONE | EXIT | Agent must read ticket status |
| R-04 | Ticket is a feature request, not security | "Add dark mode to dashboard" | EXIT | Agent must classify intent |
| R-05 | Ticket is process/policy issue, no code needed | "Update security policy document" | EXIT | No code change required |
| R-06 | Ticket has no repo URL and no file hints | Vague description only | FULL + ask for context OR graceful fail | Tests ambiguity handling |
| R-07 | Duplicate ticket pointing to already-patched code | References a commit that already fixed it | EXIT with explanation | Agent must verify current state |
| R-08 | Ticket explicitly names file AND line number | "Line 87 in UserController.java" | SKIP_RESEARCH | Research would be redundant |
| R-09 | Infrastructure ticket (Dockerfile, k8s yaml) | "Port 22 exposed in Dockerfile" | FULL or MICRO (config fix) | Tests non-Java code path |
| R-10 | CVSS ≥ 8.0 → triggers HITL gate | severity="Critical" | FULL + HITL approval required | Gate must fire before commit |

---

### Layer 2 — Research Agent Scenarios

Tests whether the agent finds the right files and relevant context.

**Happy Path**

| ID | Scenario | What to Verify |
|----|----------|---------------|
| RA-01 | Single file, vuln clearly in one function | Correct file returned, no hallucinated paths |
| RA-02 | Multi-file cascade (auth check in middleware + controller + model) | All 3 files identified, dependency order noted |
| RA-03 | Large repo (500+ files, multiple services) | Correct file found without hallucinating others |
| RA-04 | Vulnerability in a utility/helper used by 10+ callers | Root cause file identified, not just one caller |
| RA-05 | Vulnerability described in ticket using business language, not technical | Agent maps "cart total manipulation" → correct validation file |

**Edge Cases**

| ID | Scenario | Expected Behavior |
|----|----------|------------------|
| RA-06 | Ticket mentions a wrong file path | Agent finds the actual vulnerable file, not the wrong one from ticket |
| RA-07 | Vulnerability is in a third-party library (can't edit directly) | Agent flags it as dependency fix, not code fix |
| RA-08 | Vulnerability only exists in a specific environment config | Agent identifies config file, not source code |
| RA-09 | Codebase is minified/generated (bundle.js) | Agent recognizes it can't fix generated code, surfaces original source |
| RA-10 | Code comments in ticket language are misleading | Agent ignores misleading comments, reads actual code behavior |
| RA-11 | Vulnerability only manifests at runtime (race condition) | Agent correctly categorizes as hard/uncertain |
| RA-12 | Empty repository / no code yet | Graceful fail with clear message |
| RA-13 | Polyglot repo (Java + Python + Go services) | Agent searches all relevant languages |
| RA-14 | Very large single file (15k+ lines) | Agent doesn't timeout; uses targeted search |

---

### Layer 3 — Per OWASP Category Test Cases

This is the core of your golden dataset. Here's the full breakdown:

---

#### A01:2025 — Broken Access Control

| ID | Scenario | Code Pattern | Expected Fix |
|----|----------|-------------|-------------|
| BAC-01 | IDOR — user fetches another user's order by ID | `getOrder(orderId)` with no ownership check | Add `userId == order.userId` check |
| BAC-02 | Missing authorization on admin endpoint | `/admin/deleteUser` with no role check | Add `hasRole(ADMIN)` annotation |
| BAC-03 | Horizontal privilege escalation via JWT claim | JWT `role` claim trusted without server-side verification | Verify role from DB, not token |
| BAC-04 | Path traversal — `../../etc/passwd` reachable | `readFile("uploads/" + filename)` | Canonicalize + allowlist path |
| BAC-05 | CORS misconfiguration — `Access-Control-Allow-Origin: *` on sensitive endpoint | Open CORS on `/api/account/details` | Restrict to known origins |
| BAC-06 | Forced browsing — no auth on `/internal/metrics` | Endpoint exists, no middleware | Add auth middleware |
| BAC-07 | Function-level access control missing (method exposed, not just URL) | `@PostMapping` without `@PreAuthorize` | Add method-level security |
| BAC-08 | Multi-tenant data leak — query doesn't filter by tenant ID | `SELECT * FROM orders WHERE id=?` missing `AND tenant_id=?` | Add tenant scoping to query |

---

#### A02:2025 — Security Misconfiguration

| ID | Scenario | Code Pattern | Expected Fix |
|----|----------|-------------|-------------|
| SM-01 | Debug mode on in production config | `DEBUG=True` in `settings.py` | Env-based flag, False by default |
| SM-02 | Stack trace exposed in HTTP response | `return Response(str(e), 500)` | Generic error message, log internally |
| SM-03 | Directory listing enabled | `Options +Indexes` in nginx/apache config | Remove or add `autoindex off` |
| SM-04 | Missing security headers | No `Content-Security-Policy`, `X-Frame-Options` | Add security headers middleware |
| SM-05 | Default credentials in shipped code | `admin/admin` hardcoded in seed data | Remove seed, force credential change |
| SM-06 | XML External Entity (XXE) processing enabled | `DocumentBuilderFactory` with defaults | Disable external entity processing |
| SM-07 | Unnecessary HTTP methods allowed | DELETE/PUT on endpoints that shouldn't have them | Restrict to required methods only |
| SM-08 | S3 bucket / storage public by default | `ACL: public-read` in infrastructure code | Change to private, use signed URLs |

---

#### A03:2025 — Software Supply Chain Failures

| ID | Scenario | Signal | Expected Fix |
|----|----------|--------|-------------|
| SC-01 | Dependency with known critical CVE | `log4j 2.14.1` in `pom.xml` | Upgrade to patched version |
| SC-02 | Dependency with no version pin | `requests>=2.0` in `requirements.txt` | Pin to specific secure version |
| SC-03 | Transitive dependency vulnerability | Direct dep is safe, but its dependency isn't | Add explicit safe version override |
| SC-04 | Dependency from unverified source (HTTP, not HTTPS) | `http://` repo URL in build config | Switch to HTTPS + checksum verification |
| SC-05 | Package integrity not verified | No hash/checksum in lock file | Add integrity hash |
| SC-06 | Dependency confusion risk — internal package name also on public registry | Private package name collides with PyPI package | Use namespace/scope or registry config |

---

#### A04:2025 — Cryptographic Failures

| ID | Scenario | Code Pattern | Expected Fix |
|----|----------|-------------|-------------|
| CF-01 | MD5 for password hashing | `MessageDigest.getInstance("MD5")` | Replace with bcrypt/argon2 |
| CF-02 | SHA-1 for password hashing | `hashlib.sha1(password)` | Replace with bcrypt/argon2 |
| CF-03 | Hardcoded encryption key | `private static final String KEY = "abc123"` | Load from env/secrets manager |
| CF-04 | Weak random for security token | `Math.random()` for session token | Use `SecureRandom` / `secrets.token_hex()` |
| CF-05 | Sensitive data in plaintext in DB | `user.ssn` stored without encryption | Add column-level encryption |
| CF-06 | HTTP used instead of HTTPS | `http://` in API client config | Force HTTPS, HSTS header |
| CF-07 | ECB mode used for AES encryption | `Cipher.getInstance("AES/ECB/PKCS5Padding")` | Switch to AES-GCM |
| CF-08 | Short key length | `KeyGenerator.getInstance("AES"); kg.init(128)` | Enforce 256-bit minimum |
| CF-09 | Sensitive data in logs | `log.info("User password: " + password)` | Remove sensitive field from log statement |

---

#### A05:2025 — Injection

| ID | Scenario | Variant | Expected Fix |
|----|----------|---------|-------------|
| INJ-01 | Classic SQL injection | String concat in query | Parameterized query / PreparedStatement |
| INJ-02 | Blind SQL injection | Boolean-based, no direct output | Same fix; harder for agent to detect from static analysis |
| INJ-03 | NoSQL injection (MongoDB) | `db.users.find({username: req.body.username})` | Input validation + typed query |
| INJ-04 | OS command injection | `Runtime.exec("ping " + userInput)` | Allowlist input, use safe API |
| INJ-05 | LDAP injection | `"(uid=" + username + ")"` | Escape special LDAP chars |
| INJ-06 | Stored XSS | User input saved to DB, rendered without encoding | HTML-encode on output |
| INJ-07 | Reflected XSS | `response.write(request.getParam("q"))` | Encode output |
| INJ-08 | DOM-based XSS | `document.innerHTML = location.hash` | Use `textContent`, not `innerHTML` |
| INJ-09 | Server-Side Template Injection | `Template(userInput).render()` | Never render user input as template |
| INJ-10 | Log injection | `log.info("User: " + username)` where username contains newlines | Sanitize log inputs |
| INJ-11 | GraphQL injection | No query depth/complexity limits | Add depth limiting middleware |
| INJ-12 | XML injection | User-controlled XML parsed server-side | Schema validation + entity disabling |

---

#### A06:2025 — Insecure Design

| ID | Scenario | Design Flaw | Expected Fix |
|----|----------|------------|-------------|
| ID-01 | No rate limiting on login endpoint | Brute force possible | Add rate limiting middleware |
| ID-02 | Password reset sends token in URL (logged by proxies) | Token in GET param | Send token in POST body or use one-time link |
| ID-03 | No account lockout after N failed attempts | Infinite retry possible | Add lockout after 5 failures |
| ID-04 | Business logic flaw — negative cart quantity allows credit | `total = price * quantity` with no validation | Add `quantity > 0` check |
| ID-05 | Mass assignment — user can escalate own role | `User.update(request.body)` — `isAdmin` field accepted | Allowlist updatable fields |
| ID-06 | Predictable resource IDs | Sequential integer IDs for sensitive resources | Use UUIDs |
| ID-07 | No CSRF protection on state-changing endpoints | No CSRF token on POST | Add CSRF middleware |

---

#### A07:2025 — Authentication Failures

| ID | Scenario | Code Pattern | Expected Fix |
|----|----------|-------------|-------------|
| AF-01 | Hardcoded credentials in source | `if password == "admin123"` | Remove hardcode, use credential store |
| AF-02 | JWT `alg: none` accepted | Algorithm not validated server-side | Enforce algorithm allowlist |
| AF-03 | Session not invalidated on logout | `session.clear()` instead of `session.invalidate()` | Full session invalidation |
| AF-04 | Session fixation | Session ID not rotated on login | Regenerate session ID post-auth |
| AF-05 | Weak password policy | No min length / complexity check | Enforce policy in validator |
| AF-06 | No MFA for sensitive operations | Funds transfer with only password | Add step-up authentication |
| AF-07 | JWT secret is weak/default | `SECRET_KEY = "secret"` | Load strong key from env |
| AF-08 | Token expiry not enforced | JWT `exp` claim not validated | Add expiry check |
| AF-09 | Insecure "remember me" cookie | Persistent cookie with session ID, no `HttpOnly`/`Secure` | Add flags, use separate long-lived token |

---

#### A08:2025 — Software/Data Integrity Failures

| ID | Scenario | Code Pattern | Expected Fix |
|----|----------|-------------|-------------|
| DI-01 | Java deserialization of untrusted input | `ObjectInputStream.readObject()` on user data | Validate class allowlist before deserializing |
| DI-02 | Python pickle deserialization | `pickle.loads(user_data)` | Switch to JSON or validate source |
| DI-03 | No integrity check on auto-update | Downloading and executing update without signature check | Add signature verification |
| DI-04 | Missing SRI on CDN scripts | `<script src="https://cdn.example.com/lib.js">` | Add `integrity` and `crossorigin` attributes |
| DI-05 | CI/CD pipeline executes untrusted code | `npm install` from untrusted registry in CI | Pin registry + hash verification |

---

#### A09:2025 — Security Logging & Alerting Failures

| ID | Scenario | Code Pattern | Expected Fix |
|----|----------|-------------|-------------|
| LA-01 | No logging of failed auth attempts | Login failure → no log | Add structured log on failure |
| LA-02 | Password logged in plaintext | `log.debug("Login attempt: " + password)` | Remove password from log |
| LA-03 | PII in logs | `log.info("User email: " + email)` | Hash/mask PII before logging |
| LA-04 | No audit log for privilege escalation | Role change not recorded | Add immutable audit log entry |
| LA-05 | Log injection | User-controlled data in log without sanitization | Sanitize newlines/special chars |
| LA-06 | No alerting on repeated failures | 1000 failed logins → silence | Add threshold-based alerting hook |

---

#### A10:2025 — Mishandling of Exceptional Conditions

| ID | Scenario | Code Pattern | Expected Fix |
|----|----------|-------------|-------------|
| EC-01 | Stack trace in HTTP response | `return 500, traceback.format_exc()` | Return generic message, log internally |
| EC-02 | Catch-all swallows security errors | `except Exception: pass` | At minimum, log all caught exceptions |
| EC-03 | Null pointer on missing auth header leads to bypass | Missing `Authorization` header → NPE → request proceeds | Fail closed, not open |
| EC-04 | Resource exhaustion — no timeout on external calls | HTTP call with no `timeout` param | Add `timeout=30` |
| EC-05 | Integer overflow in privilege check | `if user.permissions & ADMIN_FLAG == 1` with overflow risk | Use safe arithmetic / bitwise check |
| EC-06 | Error reveals internal path | `FileNotFoundException: /home/app/secrets/.env not found` | Sanitize error message |
| EC-07 | Partial failure leaves system in insecure state | Transaction fails halfway; access already granted | Use atomic transactions |

---

### Layer 4 — Pipeline Failure Modes (Your Taxonomy Test Cases)

These specifically test that the failure taxonomy categories are correctly classified when things go wrong:

| Failure Mode | Test Scenario | How to Trigger |
|-------------|--------------|----------------|
| WRONG_FILE | Research agent picks a file that imports the vulnerable one, not the source | Use a repo where vulnerability is 2 levels deep |
| WRONG_FIX_TYPE | Agent sanitizes input instead of parameterizing query | SQL injection with complex query builder |
| BROKEN_SYNTAX | Patch has correct intent but invalid syntax | Multi-language repo; agent applies Java fix to Python file |
| PARTIAL_FIX | Agent fixes one injection point but misses a second one in same function | Two SQL queries in same method |
| NO_OUTPUT | Agent hits rate limit or Cursor CLI times out | Large file + slow LLM (mock a delay) |

---

### Layer 5 — Adversarial & Stress Cases (Your Edge Cases, Expanded)

| ID | Scenario | What It Tests |
|----|----------|--------------|
| ADV-01 | Ticket description is one sentence, no technical detail | Research agent must work from summary only |
| ADV-02 | Ticket description is in a different language (Hindi/Spanish) | Language handling |
| ADV-03 | Ticket has 5000+ character description with irrelevant content | Agent extracts signal from noise |
| ADV-04 | Required ticket fields are missing (no summary, no description) | Graceful fail with actionable error |
| ADV-05 | Ticket has conflicting info (says Java but repo is Python) | Agent trusts code over ticket text |
| ADV-06 | 3+ files need cascading changes in dependency order | Plan agent handles ordering correctly |
| ADV-07 | Fix for file A depends on fix in file B being applied first | Ordering/sequencing correctness |
| ADV-08 | Same vulnerability present in 10 different files | Agent doesn't just fix file 1 of 10 |
| ADV-09 | Vulnerability is in a comment/documentation, not actual code | Agent correctly identifies no code fix needed |
| ADV-10 | Cursor CLI returns non-zero exit code | Graceful degradation, not silent failure |
| ADV-11 | Two skills loaded with overlapping trigger conditions | Registry resolves conflict cleanly |
| ADV-12 | Token budget exceeded mid-research | Agent stops, reports partial result |
| ADV-13 | Ticket points to a private/inaccessible repo | Clear error, not hang |
| ADV-14 | Vulnerability only in test code, not production | Agent notes it but doesn't block on it |
| ADV-15 | Fix requires adding a new dependency to `pom.xml` | Plan agent captures dep change, not just code change |

---

## Putting It Together — Coverage Matrix for Your 20 Cases (Week 2)

Since the roadmap says 20 for the baseline, here's how I'd distribute them:

| Category | Easy | Medium | Hard | Total |
|----------|------|--------|------|-------|
| A05 Injection (SQL, XSS, Command) | 2 | 2 | 1 | 5 |
| A01 Broken Access Control | 1 | 1 | 1 | 3 |
| A04 Cryptographic Failures | 1 | 1 | 0 | 2 |
| A07 Authentication Failures | 1 | 1 | 0 | 2 |
| A02 Security Misconfiguration | 1 | 1 | 0 | 2 |
| A03 Supply Chain | 1 | 0 | 0 | 1 |
| A10 Exception Handling | 1 | 1 | 0 | 2 |
| Orchestrator routing (EXIT/MICRO) | 2 | 0 | 0 | 2 |
| Adversarial edge case | 0 | 0 | 1 | 1 |
| **Total** | **10** | **7** | **3** | **20** |

The 40-case expansion in Phase 3 then skews heavily toward hard cases and the categories where your baseline scores are weakest.

---

One practical tip: for each dataset example, also store a `repo_snapshot_commit_hash` so your golden cases are reproducible. If the test repo changes, your expected outputs drift. The cases should be immutable once baselined — exactly as your roadmap already says.


Good, I have everything I need. Let me compile this into a clean, decision-ready reference for you.

---

## The Complete Repo Map for IntuitionX Testing

The key insight before jumping into the list: **don't try to use all of these**. You need to pick a primary repo per language/category and fork it into your own org so your golden dataset cases reference a stable commit SHA that never drifts. Here's exactly how to think about it.

---

### Tier 1 — Your Core Repos (Use These First)

These are the ones you should fork immediately and pin a commit hash for your LangSmith dataset.

---

#### 1. DVPWA — Damn Vulnerable Python Web App
**GitHub:** `github.com/anxolerd/dvpwa`
**Language:** Python (aiohttp + PostgreSQL)
**Why it's your primary:** You've already run your research agent against it and scored 4.5/5. It's your known baseline.

| OWASP Category | Specific Vulns Present |
|----------------|----------------------|
| A05 Injection | SQL injection via string concat in student search |
| A04 Crypto | MD5 password hashing (the one your agent missed) |
| A02 Misconfiguration | Debug settings, stack traces in responses |
| A10 Exception Handling | Unhandled exceptions exposing internals |

**Your test cases from it:** INJ-01 (SQL injection), CF-01 (MD5 hashing), EC-01 (stack trace in response), SM-02 (debug error exposure). Easy and medium difficulty, small codebase → MICRO pipeline route.

---

#### 2. OWASP WebGoat
**GitHub:** `github.com/WebGoat/WebGoat`
**Language:** Java (Spring Boot, Maven)
**Why it's essential:** WebGoat is a deliberately insecure application that allows developers to test vulnerabilities commonly found in Java-based applications that use common and popular open source components. It covers almost the entire OWASP list in one repo and is actively maintained. WebGoat contains lessons for almost all OWASP Top 10 vulnerabilities and more.

| OWASP Category | Specific Vulns Present |
|----------------|----------------------|
| A01 Broken Access Control | IDOR on user accounts, path traversal, missing auth on admin endpoints |
| A05 Injection | SQL injection (multiple variants), XSS (stored, reflected, DOM) |
| A07 Auth Failures | JWT `alg:none` bypass, session fixation, weak secrets |
| A08 Integrity Failures | Java deserialization of untrusted input |
| A09 Logging Failures | Missing auth failure logging, PII in logs |
| A04 Crypto | Weak hashing, hardcoded keys |

**Size:** Large repo (500+ files) → FULL pipeline route. Great for testing your research agent's file-finding at scale.

---

#### 3. OWASP Juice Shop
**GitHub:** `github.com/juice-shop/juice-shop`
**Language:** Node.js / TypeScript / Angular
**Why it's essential:** Juice Shop looks exactly like a real online shop — which is what makes it the best bug bounty simulator of the three. It has 100+ challenges covering every OWASP category, all tagged with difficulty levels.

| OWASP Category | Specific Vulns Present |
|----------------|----------------------|
| A01 Access Control | IDOR on order IDs, admin endpoint exposure, CORS misconfiguration |
| A06 Insecure Design | No rate limiting on login, predictable IDs, business logic flaws (negative quantities, coupon reuse) |
| A07 Auth Failures | JWT secret is `"secret"`, no MFA, broken session management |
| A05 Injection | DOM-based XSS, SQL injection, NoSQL injection |
| A04 Crypto | Sensitive data in localStorage, weak password policy |

**Size:** Large, realistic codebase → FULL pipeline. Also perfect for your adversarial test ADV-04 (business logic flaws) and ADV-01 (realistic-looking non-security ticket).

---

#### 4. DVWA — Damn Vulnerable Web Application
**GitHub:** `github.com/digininja/DVWA`
**Language:** PHP / MariaDB
**Why it's useful:** The aim of DVWA is to practice some of the most common web vulnerabilities, with various levels of difficulty, with a simple straightforward interface. There are both documented and undocumented vulnerabilities with this software. Each vulnerability module has Low/Medium/High difficulty settings baked into the source code — which means the same file has three different vulnerability levels you can point your agent at.

| OWASP Category | Specific Files to Use |
|----------------|----------------------|
| A05 Injection | `vulnerabilities/sqli/source/low.php` (obvious), `medium.php` (obfuscated) |
| A05 XSS | `vulnerabilities/xss_r/` (reflected), `xss_s/` (stored), `xss_d/` (DOM) |
| A02 Misconfiguration | `vulnerabilities/fi/` (file inclusion), directory listing enabled |
| A01 Access Control | `vulnerabilities/idor/` |
| A07 Auth | Brute-force module at `vulnerabilities/brute/` |

**Key eval use:** Generating easy/medium/hard variants of the same vuln class from a single repo. Point your agent at `low.php` vs `high.php` for difficulty scaling.

---

#### 5. VAmPI — Vulnerable REST API
**GitHub:** `github.com/erev0s/VAmPI`
**Language:** Python (Flask) — REST API only
**Why it's valuable:** VAmPI includes a switch on/off to allow the API to be vulnerable or not while testing. This allows better coverage of cases for false positives and negatives. That toggle is gold for your evaluator — you can run the same test against the "secure" version and verify your agent doesn't produce a false positive.

| OWASP Category | Specific Vulns Present |
|----------------|----------------------|
| A01 Access Control | IDOR on user data endpoints, broken object-level auth |
| A07 Auth Failures | JWT with weak key, missing token validation |
| A05 Injection | SQL injection via API params |
| A02 Misconfiguration | Excessive data exposure in API responses |
| A09 Logging | No logging of failed auth attempts |

**Size:** Small, single-service codebase → MICRO pipeline. Perfect for testing the SKIP_RESEARCH route.

---

### Tier 2 — Language Coverage Repos

Use these to expand language diversity beyond Python/Java for your 40-case Phase 3 dataset.

---

#### 6. OWASP NodeGoat
**GitHub:** `github.com/OWASP/NodeGoat`
**Language:** Node.js + MongoDB
**Coverage:** NodeGoat provides an environment to learn how OWASP Top 10 security risks apply to web applications developed using Node.js.

Key vulns: NoSQL injection (INJ-03 in your taxonomy), Stored XSS, prototype pollution, CSRF, insecure deserialization. Also notable: it uses MongoDB, which is your only publicly available source for NoSQL injection test cases.

---

#### 7. OWASP RailsGoat
**GitHub:** `github.com/OWASP/railsgoat`
**Language:** Ruby on Rails
**Coverage:** RailsGoat demonstrates real-world security vulnerabilities from the OWASP Top 10, including SQL injection, cross-site scripting, authentication issues, insecure direct object references, and more. Has been updated through Rails 8, so it's current. Use for: mass assignment vulnerability (ID-05 in your taxonomy) — Rails' strong parameters pattern makes this uniquely testable here.

---

#### 8. JavaSecLab
**GitHub:** `github.com/whgojp/JavaSecLab`
**Language:** Java (Spring Boot ecosystem)
**Why it's different from WebGoat:** JavaSecLab covers business logic flaws including IDOR, captcha security, payment security, and concurrency security, plus JWT credential security, and Spring Boot ecosystem exposure including Swagger, Actuator, Druid, and MySQL JDBC deserialization. Also covers Log4j2 and Shiro — real-world Java component CVEs — making it your best source for A03 supply chain test cases in Java.

---

#### 9. Damn Vulnerable GraphQL Application (DVGA)
**GitHub:** `github.com/dolevf/Damn-Vulnerable-GraphQL-Application`
**Language:** Python (GraphQL)
**Coverage:** DVGA is an intentionally vulnerable implementation of Facebook's GraphQL technology, to learn and practice GraphQL Security.

Use for: INJ-11 (GraphQL injection, no depth/complexity limits), introspection abuse, batching attacks. The only dedicated GraphQL vuln source in this list.

---

#### 10. vuln-spring (SAST-targeted Spring app)
**GitHub:** `github.com/malikashish8/vuln-spring`
**Language:** Java (Spring)
**Coverage:** Intentionally vulnerable Spring application designed specifically to test SAST tools. Because it's designed to fool SAST scanners rather than just be obviously vulnerable, it's a good source of your WRONG_FIX_TYPE and PARTIAL_FIX failure mode test cases — the vulns are subtle enough that an agent might find the file but get the fix type wrong.

---

### Tier 3 — Specialty Coverage (Fill Specific Gaps)

---

#### 11. WrongSecrets
**GitHub:** `github.com/OWASP/wrongsecrets`
**Language:** Java + Docker/K8s configs
**OWASP:** A04 (hardcoded secrets), A03 (supply chain), A02 (misconfiguration)

WrongSecrets is "Vulnerable by Design" to show how to not handle secrets in Docker, Kubernetes and in the cloud (AWS/GCP/Azure). This is your primary source for CF-03 (hardcoded keys), AF-07 (weak JWT secrets), and SM-08 (cloud misconfig) cases — it has secrets in Docker env vars, K8s secrets, and code simultaneously.

---

#### 12. leaky-repo (Gitleaks benchmark)
**GitHub:** Referenced in gitleaks docs as `~/leaky-repo/` test fixture
**Language:** Multi-language
**OWASP:** A04 (secrets in code), A03 (supply chain)

leaky-repo is a benchmarking repo for secrets scanning. Purely for hardcoded secrets test cases: API keys, DB passwords, AWS credentials in source code. Use for CF-03, AF-01, and your ADV-15 case (fix requires touching pom.xml/requirements.txt, not just code).

---

#### 13. Swiss Cheese Software
**GitHub:** Listed in `github.com/kaiiyer/awesome-vulnerable`
**Language:** Node.js + Python + Rust (multi-language)
**OWASP:** A03 (supply chain), A04 (secrets)

Swiss Cheese Software is an intentionally vulnerable multi-language app with hardcoded secrets and outdated dependencies, designed to test supply chain security scanners, dependency auditors, and SBOM generators.

Use for: your SC-01 through SC-06 supply chain cases, plus your ADV-13 adversarial case (polyglot repo — Java + Python + Go). This is the only multi-language repo in the list that specifically targets supply chain detection.

---

#### 14. CentralBank-SecLand-Vulnerable (Spring Boot banking)
**GitHub:** `github.com/careb36/careb36-BancoCentral-SecLand-Vulnerable`
**Language:** Java (Spring Boot)
**OWASP:** A01 (IDOR), A05 (SQL Injection)

This is a deliberately vulnerable Spring Boot banking application that simulates user registration, authentication, account management and transfers, and includes intentional SQL injection and IDOR flaws for controlled security testing. The banking domain is valuable because it creates natural multi-tenant test scenarios (BAC-08 in your taxonomy) and business logic flaws (ID-04 negative balance, ID-06 predictable account IDs).

---

#### 15. Vulnerable Flask API (michealkeines)
**GitHub:** `github.com/michealkeines/Vulnerable-API`
**Language:** Python (Flask + Jinja + SQLite3)
**OWASP:** A05 (XSS, SQLi, SSTI), A02 (misconfiguration)

This purposely flawed Python app contains intentional security vulnerabilities including XSS, SQLi, HHI, LFI, RFI, and SSTI. The Server-Side Template Injection (SSTI) coverage is unique — INJ-09 in your taxonomy (Jinja2 template injection). No other repo in this list gives you clean Python SSTI code to test against.

---

### The Complete Mapping Matrix

Here's the definitive table mapping every OWASP category to the repos that cover it:

| OWASP Category | Primary Repo | Backup Repo | Language Coverage |
|----------------|-------------|-------------|-------------------|
| A01 Broken Access Control | Juice Shop | WebGoat, CentralBank | JS, Java |
| A02 Misconfiguration | DVWA | WrongSecrets, DVPWA | PHP, Docker, Python |
| A03 Supply Chain | Swiss Cheese | WrongSecrets, JavaSecLab | Multi, Java |
| A04 Crypto Failures | WebGoat | WrongSecrets, DVPWA | Java, Docker, Python |
| A05 Injection (SQL) | DVWA | DVPWA, WebGoat | PHP, Python, Java |
| A05 Injection (XSS) | Juice Shop | DVWA, Vuln-Flask-API | JS, PHP, Python |
| A05 Injection (SSTI) | Vuln-Flask-API | DVGA | Python |
| A05 Injection (NoSQL) | NodeGoat | VAmPI | Node.js, Python |
| A05 Injection (GraphQL) | DVGA | — | Python |
| A06 Insecure Design | Juice Shop | RailsGoat | JS, Ruby |
| A07 Auth Failures | WebGoat | VAmPI, Juice Shop | Java, Python, JS |
| A08 Integrity Failures | WebGoat | JavaSecLab | Java |
| A09 Logging Failures | VAmPI | WebGoat | Python, Java |
| A10 Exception Handling | DVPWA | WebGoat | Python, Java |

---

### Practical Setup: What to Actually Do This Week

**Step 1 — Fork these 5 into your org and pin commit SHAs immediately:**
```
your-org/dvpwa          → pin current main HEAD
your-org/WebGoat        → pin latest release tag (2023.8)
your-org/juice-shop     → pin latest release tag
your-org/VAmPI          → pin current main HEAD
your-org/wrongsecrets   → pin current main HEAD
```

**Step 2 — For each test case in your LangSmith dataset, add this field:**
```python
"repo_snapshot": {
    "url": "https://github.com/your-org/dvpwa",
    "commit_sha": "a3f9c1d...",   # never change this
    "target_file": "app/views/students.py",
    "target_lines": [42, 67]
}
```

**Step 3 — Don't run your agent against live clones.** For each case, snapshot the specific file(s) as test fixtures in your `/evals/golden/` directory. That way your dataset is fully self-contained even if the upstream repo adds a commit.

**Step 4 — Difficulty mapping to repo:**

| Difficulty | Repo | Why |
|-----------|------|-----|
| Easy | DVPWA, VAmPI | Small, obvious, single file |
| Medium | DVWA (Medium mode), VAmPI | Slightly obfuscated, 2-file |
| Hard | WebGoat, Juice Shop, JavaSecLab | Large repo, multi-file cascade, subtle |

Your 20-case baseline should draw from: DVPWA (5 cases you already understand), VAmPI (4 cases), WebGoat (6 cases), Juice Shop (3 cases), WrongSecrets (2 cases). That gives you Python + Java + JS coverage across all 10 OWASP categories before you even touch the Tier 2 repos.

