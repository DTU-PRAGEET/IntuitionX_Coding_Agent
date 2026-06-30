---
name: injection
module: security
description: Classify and remediate Injection — SQLi, XSS, command injection, LDAP/XML injection
agents: [research, planner, implement]
trigger_labels: injection, sqli, sql-injection, xss, command-injection, ldap-injection, nosql-injection
---

# Injection (OWASP A03)

## Definition

Untrusted data is sent to an interpreter or renderer as part of a command, query, template, or script — without separation of data and code. The interpreter executes or renders attacker-controlled content.

**CWE:** CWE-89, CWE-79, CWE-77, CWE-78, CWE-90, CWE-943

---

## Positive Indicators (classify as Injection)

Classify the finding as Injection when **all** of the following hold:

1. Attacker-controlled input reaches a **sink** (interpreter or HTML renderer)
2. Input is concatenated, interpolated, or passed unsanitized into that sink

| Sub-type | Code-level indicators (match ANY) |
|----------|-----------------------------------|
| **SQLi** | `"SELECT ... " + userInput`, f`"...{var}..."` passed to `execute`/`query`, `%` formatting in SQL strings, MyBatis `${}` with user data |
| **XSS** | `autoescape=False`, `|safe` on user variables, `Markup(userInput)`, `innerHTML = userInput`, `document.write(userInput)`, unescaped `{{ user_field }}` in HTML context |
| **Command injection** | `Runtime.exec(userInput)`, `os.system(userInput)`, `subprocess.call(..., shell=True)` with user strings, backtick/shell expansion with request params |
| **LDAP injection** | LDAP filter built with `"(&...(uid=" + username + ")..."` |
| **NoSQL injection** | Mongo query `{"$where": userInput}`, `$gt`/`$ne` injected via unsanitized JSON |

---

## Negative Indicators (does NOT qualify)

Do **not** classify as Injection when:

| Condition | Why excluded |
|-----------|--------------|
| SQL string is static with zero user/request variables | No untrusted data in query |
| Parameterized query: `PreparedStatement`, `?` placeholders, SQLAlchemy `text()` with bound params | Data separated from query |
| Output is JSON API with `Content-Type: application/json` and no HTML rendering | Not a rendering sink |
| `|safe` applied only to server-generated static HTML, not user input | No user data in sink |
| `Runtime.exec` with fixed command array, no user string in command | No injection vector |
| Input validated against strict allowlist before reaching sink | Control prevents injection (may be other class) |
| HTML entity encoding applied at output boundary for all user fields | XSS mitigated at sink |

---

## Investigation — Research Agent

1. `grep -rn` for sink patterns from Positive Indicators table
2. `read` each hit — trace variable from HTTP param/header to sink
3. One `cursor_search` only if grep finds nothing and paths unknown
4. Emit one hypothesis per confirmed injection sub-type

**Done when:** Each injection vector has hypothesis + affected file with line evidence.

---

## Planning — Planner Agent

1. One step block per injection hypothesis (1:1 mapping)
2. EDIT must change sink to parameterized query / encoding / allowlist
3. Files needing no edit (mitigated upstream) → READ→VERIFY only
4. NEVER emit EDIT with identical `old_string` and `new_string`

**Done when:** Every injection hypothesis has steps; all EDIT patches differ.

---

## Implementation — Executor Agent

1. Batch all injection fixes into **one** `CursorGateway.edit()` call
2. VERIFY: forbidden patterns absent (`+ userInput` in SQL, `|safe` on user fields, etc.)

**Done when:** VERIFY passes for every injection hypothesis.

---

## Recommended Fixes

- SQL: parameterized queries / ORM bound parameters
- XSS: enable autoescape; encode output; remove `|safe` on user data
- Command: avoid shell; use argument arrays; allowlist commands
- LDAP: parameterized filters / escape functions

---

## Validation Strategy

- VERIFY grep confirms vulnerable construct removed at each sink
- Re-run SAST rule ID if available
- Regression test with malicious payload that previously succeeded
