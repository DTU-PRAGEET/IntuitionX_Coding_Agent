---
name: java-sast-fixes
module: security
description: Fix Java SAST findings — SQL injection, XSS, path traversal, insecure deserialization
agents: [research, planner, implement]
fix_types: [parameterize_sql, sanitize_input, xss_encoding, auth_check]
trigger_labels: security, sast, sqli, sql-injection, xss
trigger_languages: java
---

<!-- CHANGELOG (2026-06-26): Added per-phase decision flows, competing fix-type resolution, framework edge cases, and explicit completion criteria per phase. -->

## description

Domain skill for remediating Java static analysis (SAST) findings. Covers OWASP Top 10 patterns commonly flagged by Checkmarx, SonarQube, and Fortify.

## trigger_conditions

Activate when:
- Ticket labels include `security`, `sast`, `sqli`, or `xss`
- Affected language is Java
- Fix type is `parameterize_sql`, `sanitize_input`, or `xss_encoding`

## prompt_templates

### Research — Decision Flow
1. `cursor_search` for vulnerability pattern + affected class from ticket
2. If SQLi suspected → search string concatenation in SQL/JDBC/JPQL
3. If XSS → search unencoded output in JSP/Thymeleaf/controllers
4. **Done when:** ≥1 affected file with snippet evidence OR flag `sast_evidence_sparse`

### Planner — Decision Flow
1. Map each affected file to READ→EDIT→VERIFY triad
2. **Fix type priority:** parameterize_sql > sanitize_input > xss_encoding (when multiple apply)
3. Spring JPA: prefer `@Query` bind params; raw JDBC: `PreparedStatement`
4. **Done when:** ExecutionPlan with intent_markers matching fix type

### Implement — Decision Flow
1. CursorGateway.edit per plan step — parameterize SQL or encode output at sink
2. **Done when:** VERIFY steps pass; no string-concat SQL in edited files

## implementation_guidance

1. **SQL Injection:** Replace dynamic SQL with parameterized queries. Spring: `@Param`; JDBC: `PreparedStatement.setString()`.
2. **XSS:** OWASP Java Encoder at HTML/JS/URL sink.
3. **Path Traversal:** Allowlist paths; reject `..`.
4. **Insecure Deserialization:** Avoid `ObjectInputStream` on untrusted data.

### Competing fix types
| Situation | Choose |
|-----------|--------|
| SQLi + input validation both flagged | parameterize_sql (fix at sink) |
| XSS in template vs controller | Fix at output sink (template if source of HTML) |
| MyBatis `${}` vs `#{}` | Replace `${}` with `#{}` where possible |

### Framework edge cases
| Framework | Note |
|-----------|------|
| Spring Data JPA | Use `@Query` with `:param` bindings |
| MyBatis | Use `#{}` not `${}` for user input |
| JSP | Use JSTL `<c:out>` or encoder |
| False positive (static SQL) | Flag `possible_false_positive`; minimal change |

## examples

### SQLi fix
```java
// Before
String q = "SELECT * FROM users WHERE id = " + userId;
// After
PreparedStatement ps = conn.prepareStatement("SELECT * FROM users WHERE id = ?");
ps.setString(1, userId);
```

## references

- OWASP SQL Injection Prevention Cheat Sheet
- OWASP XSS Prevention Cheat Sheet
- `skills/research/references/security_patterns.md`

## test_cases

- **sqli_parameterize**: DAO with string-concat SQL → `PreparedStatement` or named params in patch
- **xss_encode_output**: Unencoded user input in HTML → context-appropriate encoding
- **path_traversal_block**: User path input → validation rejecting `..`

## completion_criteria

| Phase | Done when |
|-------|-----------|
| Research | Root cause + affected Java files with search evidence |
| Planner | ExecutionPlan with parameterized/encoded fix per file |
| Implement | VERIFY passes; vulnerable pattern absent in diff |
