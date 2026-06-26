---
name: java-sca-fixes
module: security
description: Fix Java SCA findings — vulnerable dependencies, CVE remediation, lockfile updates
agents: [research, planner, implement]
fix_types: [dependency_upgrade, other]
trigger_labels: security, sca, cve, vulnerability
trigger_languages: java
---

<!-- CHANGELOG (2026-06-26): Added per-phase decision flows, Maven/Gradle detection, transitive conflict handling, build-break recovery, and completion criteria. -->

## description

Domain skill for remediating Software Composition Analysis (SCA) findings in Java/Maven/Gradle projects. Covers CVE-driven dependency upgrades and transitive dependency conflicts.

## trigger_conditions

Activate when:
- Ticket labels include `sca`, `cve`, or `vulnerability`
- Ticket references a CVE ID or vulnerable library version
- Fix type is `dependency_upgrade`

## prompt_templates

### Research — Decision Flow
1. Detect build system: `cursor_search("pom.xml")` OR `cursor_search("build.gradle")` OR `gradle/libs.versions.toml`
2. Search for vulnerable artifact name + version from CVE/ticket
3. Identify transitive dependents via search hits on dependency blocks
4. **Done when:** dependency file path + current version identified OR flag `sca_manifest_not_found`

### Planner — Decision Flow
1. Propose minimum safe version from CVE advisory (NVD)
2. List direct bump + any `<dependencyManagement>` / Gradle constraint needed
3. If multiple CVEs → prioritize by severity (critical first)
4. **Done when:** EDIT steps target manifest file(s) with exact version strings

### Implement — Decision Flow
1. CursorGateway.edit version pins in pom.xml / build.gradle / libs.versions.toml
2. Regenerate lockfile if project uses Gradle lockfile
3. **Done when:** VERIFY shows new version; no conflicting duplicate versions in grep

## implementation_guidance

1. **Version bump:** Direct dependency to patched advisory version.
2. **Transitive override:** Maven `<dependencyManagement>` or Gradle `constraints`.
3. **Lockfiles:** Regenerate `gradle.lockfile` after bump.
4. **Compatibility:** Check release notes; adjust imports if package relocated.

### Build system detection
| File found | Build system |
|------------|--------------|
| `pom.xml` | Maven |
| `build.gradle` / `build.gradle.kts` | Gradle |
| `gradle/libs.versions.toml` | Gradle version catalog |

### Edge cases
| Situation | Next action |
|-----------|-------------|
| Patched version breaks compilation | Try next patch minor; flag `upgrade_breaks_build`; document in self_healing_notes |
| Unresolvable transitive conflict | Add exclusion + safe replacement; flag `transitive_override` |
| CVE fixed only in major version | Bump major; list API migration steps in plan constraints |
| Multiple vulnerable transitives | Single constraint block forcing safe versions |

## examples

### Maven CVE fix
```xml
<dependency>
  <groupId>org.apache.logging.log4j</groupId>
  <artifactId>log4j-core</artifactId>
  <version>2.17.1</version>
</dependency>
```

## references

- NVD CVE database
- Maven Central / Gradle Plugin Portal

## test_cases

- **cve_version_bump**: log4j-core 2.14.0 → ≥2.17.1 in manifest
- **transitive_override**: Vulnerable transitive → constraint or exclusion
- **lockfile_regenerated**: Gradle lockfile updated after bump

## completion_criteria

| Phase | Done when |
|-------|-----------|
| Research | Vulnerable artifact + manifest path + current version documented |
| Planner | ExecutionPlan edits manifest with target safe version |
| Implement | VERIFY confirms new version; lockfile committed if applicable |
