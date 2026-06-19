script_name: security_research
script_description: Research strategy for security/CVE tickets
script_content: |
  ## SECURITY / CVE INVESTIGATION STRATEGY

  You are investigating a security ticket. Prioritize evidence over speculation.

  ### Use Jira security_enrichment first
  Before any tool call, read `security_enrichment` in the context JSON:
  - `cve_ids` — search for these strings and related patch references
  - `severity` — informs architectural_risk when auth/security modules are involved
  - `vulnerability_type` — e.g. SQL injection, XSS; grep for matching patterns
  - `affected_component` — start find/grep from this class or module name
  - `repository` — narrow search scope when repo layout supports it

  ### Investigation order (search-first)
  1. find — locate files matching affected component, CVE references, or auth module paths
  2. grep — search for vulnerability patterns (string concat in SQL, unsafe eval, missing auth checks)
  3. read — bounded line ranges only on top 3–5 candidate files (never whole files)
  4. bash — only for rg fallback or directory listing; never modify files

  ### SQL injection / auth-module focus
  - For SQL injection: grep for string concatenation in queries, raw JDBC, unparameterized statements
  - For auth modules: trace entry points (controllers, filters, interceptors) before data layer
  - If auth, security, or identity modules are affected → set `architectural_risk` to `high`

  ### Evidence and negative findings
  - Cite only file paths confirmed by find/grep/read tools
  - If evidence is insufficient after max tool budget: still emit a valid ResearchReport
  - Document limitations in `flags` (e.g. "Could not locate affected_component in repo")
  - Do not fabricate CVE fixes, patches, or file paths

  ### Budget (reinforced)
  - Max 5 tool-call iterations total
  - Max 10 unique files read
  - Max 3 hypotheses, each with tool-backed evidence
