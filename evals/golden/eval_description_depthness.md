# Golden Evaluation Ticket Specificity Review (G-01 → G-18)

## Objective

Review the realism of the Golden evaluation tickets by analyzing only what the agent sees:

- `ticket.title`
- `ticket.problem_statement`

**Note:** Hidden fields like `expected_files`, `pass_criteria`, or grading metadata are **not** considered.

---

# Ticket Specificity Categories

| Category | Meaning |
|----------|---------|
| **Too Specific** | Ticket names exact file(s), class, handler, or configuration file. Research is mostly skipped because the location is already revealed. |
| **Moderately Specific** | Ticket identifies an endpoint, feature, or code pattern. Research still needs to locate the implementation. |
| **Vague / Realistic** | Ticket only describes the vulnerability and business impact. The agent must discover the affected code itself. |

---

# Too Specific (Research is Mostly Bypassed)

These tickets reveal implementation details that would rarely appear in a real security ticket.

| Case | Why it's too specific |
|------|-----------------------|
| **G-04** | Title explicitly names `sqli/app.py` and `run.py`; problem statement also mentions both files and `debug=True`. |
| **G-06** | Names `api_views/users.py`, `PUT /users/v1/{username}/password`, and explains the JWT vs path validation logic. |
| **G-07** | Names `models/user_model.py`, `get_user`, `GET /users/v1/{username}`, and identifies the vulnerable f-string query. |
| **G-08** | Directly names `config.py`, `HS256`, and the hardcoded signing key. |
| **G-09** | Names `api_views/users.py`, `POST /users/v1/login`, and even includes exact error messages. |
| **G-17** | Explicitly points to `src/main/resources/application.properties` and the `password=` property. |
| **G-18** | Directly identifies `k8s/secrets-secret.yml` and states that base64 secrets are committed there. |

### Observation

G-17 and G-18 read more like remediation instructions than Jira tickets.

The VAmPI cases (G-04, G-06, G-07, G-08, G-09) have the same issue by exposing implementation details before research begins.

---

# Moderately Specific (Some Discovery Required)

These tickets narrow the search space but still require meaningful code discovery.

| Case | What the ticket reveals | What research still needs to discover |
|------|--------------------------|----------------------------------------|
| **G-01** | SQL Injection in "Add Student" feature via name field | Locate `sqli/dao/student.py` |
| **G-03** | Course review XSS caused by disabled autoescape | Find `sqli/app.py` and `course.jinja2` |
| **G-05** | Admin actions protected only by UI restrictions | Identify affected handlers in `sqli/views.py` |
| **G-10** | Account search uses string concatenation in SQL | Locate `SqlInjectionLesson5a.java` |
| **G-12** | IDOR in `/profile/{userId}` endpoint | Find `IDORViewOtherProfile.java` |
| **G-13** | Upload uses client-controlled filename | Locate `ProfileUploadBase.java` |
| **G-14** | Search endpoint vulnerable through `innerHTML` | Find `search-result.component.ts` |

---

# Vague / Realistic (Closest to Real Security Tickets)

These tickets describe only the vulnerability and its impact.

The agent must determine:

- where the issue exists,
- which files are affected,
- and how to fix it.

| Case | Ticket style |
|------|--------------|
| **G-02** | Passwords use fast unsalted hashing. No algorithm or file mentioned. |
| **G-11** | JWT refresh accepts `alg:none` or unverified tokens. No implementation details. |
| **G-15** | Regular customers can access admin functionality. No file names given. |
| **G-16** | JWT verification does not pin the algorithm. No library or file disclosed. |

These are the closest to real-world security tickets where security teams know **what** is wrong but not **where** the bug exists.

---

# Summary

| Category | Cases | Count |
|----------|-------|------:|
| **Too Specific** | G-04, G-06, G-07, G-08, G-09, G-17, G-18 | **7** |
| **Moderately Specific** | G-01, G-03, G-05, G-10, G-12, G-13, G-14 | **7** |
| **Vague / Realistic** | G-02, G-11, G-15, G-16 | **4** |

---

# Common Anti-Patterns

The following patterns reduce the realism of evaluation tickets:

## 1. File Paths in the Ticket

Examples:

- G-04
- G-17
- G-18

Instead of describing the vulnerability, the ticket tells the agent exactly where to edit.

---

## 2. File + Handler Together

Examples:

- G-06
- G-07
- G-09

These reveal both the endpoint and the implementation file, leaving little work for research.

---

## 3. Exact Error Strings or Code Snippets

Examples:

- G-09
- G-13

Including implementation details or code snippets makes the ticket behave more like a debugging guide than a security report.

---

## 4. Configuration File Names

Example:

- G-08

Naming `config.py` immediately identifies where the fix belongs.

---

# Good Examples

## G-02

> User passwords are verified against a fast, unsalted hash. We need login verification to use a modern, salted, adaptive password hashing scheme.

Why it's good:

- No file names
- No algorithm names
- No implementation hints
- Research must discover everything

---

## G-15

> The administration section can be reached by a regular customer. Admin access must be enforced with a real server-side role check.

Why it's good:

- Describes business impact
- Does not reveal affected files
- Requires discovery before planning

---

# Suggested Rewrite Direction

| Case | Current Style | More Realistic Ticket |
|------|---------------|-----------------------|
| **G-17** | "Plaintext secret in application.properties" | "A production credential appears to be committed in application configuration and loaded during startup." |
| **G-18** | "Base64 secret committed in k8s/secrets-secret.yml" | "Kubernetes deployment secrets may be stored in version control in recoverable form." |
| **G-06** | File name + endpoint | "Users can change another user's password by manipulating the request." |
| **G-04** | `debug=True` in named files | "Application errors expose stack traces and internal implementation details." |

---

# Recommendation

The **ticket presented to the agent** should contain only:

- Business impact
- Security vulnerability
- Expected behavior

Implementation details such as:

- file paths,
- handler names,
- class names,
- configuration files,
- exact code snippets,

should remain only in hidden evaluation metadata such as:

- `expected_files`
- `pass_criteria`
- grading logic

This makes the benchmark significantly closer to real-world security tickets while preserving deterministic evaluation.