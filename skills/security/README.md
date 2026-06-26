# Security Skills Module

Team-specific security remediation skills for SAST and SCA findings.

## Skills

- **java-sast-fixes.md** — Static analysis fixes (SQLi, XSS, path traversal)
- **java-sca-fixes.md** — Dependency/CVE remediation for Java projects

## Usage

These skills activate automatically when ticket labels match `trigger_conditions` (e.g. `security`, `sast`, `sca`). The Implementation Agent receives `implementation_guidance` during `CursorGateway.edit()`.

## Adding a skill

Place a new `.md` file in this directory. Include `trigger_conditions`, `prompt_templates`, and `test_cases` sections. No code changes required.
