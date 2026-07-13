---
name: java-dependency-vulns
module: security
description: Remediate Java dependency vulnerabilities — CVE triage, version resolution, BOM alignment, and transitive conflict resolution
agents: [research, planner, implement]
fix_types: [dependency_upgrade, dependency_constraint, other]
trigger_labels: security, sca, cve, vulnerability, dependency
trigger_languages: java, kotlin
allowed_file_globs:
  - "pom.xml"
  - "build.gradle"
  - "build.gradle.kts"
  - "gradle/libs.versions.toml"
  - "gradle.lockfile"
  - "settings.gradle*"
  - "!**/target/**"
  - "!**/.gradle/**"
---

## description

Domain skill for **dependency-level vulnerabilities** in Java/Kotlin projects. Stress-tests the skills API surface: `trigger_labels`, `fix_types`, `allowed_file_globs`, structured `test_cases`, and multi-manifest layouts (Maven, Gradle, version catalogs).

Covers:
- Direct dependency CVE bumps
- Transitive dependency overrides (`dependencyManagement`, Gradle constraints)
- BOM / platform alignment (`spring-boot-dependencies`, Micronaut BOM)
- Kotlin DSL (`build.gradle.kts`) and version catalogs (`libs.versions.toml`)

Distinct from `java-sca-fixes` (general SCA workflow) — this skill focuses on **complex dependency graphs** and API stress cases.

## trigger_conditions

Activate when:
- Ticket labels include `dependency`, `sca`, `cve`, or `vulnerability`
- Ticket references a vulnerable Maven/Gradle coordinate or CVE advisory
- Fix type is `dependency_upgrade` or `dependency_constraint`
- Language hints: Java or Kotlin in labels or problem statement

## prompt_templates

### Research — Decision Flow
1. Detect build system: search `pom.xml`, `build.gradle(.kts)`, `libs.versions.toml`
2. Locate vulnerable `groupId:artifactId:version` from ticket/CVE text
3. Map transitive dependents via dependency tree hints in manifests
4. **Done when:** manifest path + current version + advisory target version documented

### Planner — Decision Flow
1. Choose minimum safe version from NVD/advisory
2. Prefer BOM bump when CVE fixed in parent BOM (Spring Boot, Micronaut)
3. Add `<dependencyManagement>` or Gradle `constraints` for transitive-only CVEs
4. Include VERIFY grep for new version string in manifest
5. **Done when:** EDIT steps target only files in `allowed_file_globs`

### Implement — Decision Flow
1. Apply version bumps via CursorGateway.edit (batched manifest edits)
2. Regenerate lockfile when `gradle.lockfile` present
3. **Done when:** VERIFY confirms version; no duplicate conflicting coordinates

## implementation_guidance

### Version resolution priority
| Situation | Action |
|-----------|--------|
| CVE in direct dependency | Bump `<version>` / catalog alias |
| CVE in transitive only | `dependencyManagement` or Gradle constraint |
| Fixed in newer BOM | Bump BOM import version first |
| Major version jump | Document API migration in plan constraints |

### Manifest patterns
| Build system | Files to edit |
|--------------|---------------|
| Maven | `pom.xml`, optional `gradle.properties` for versions |
| Gradle Groovy | `build.gradle`, `settings.gradle` |
| Gradle Kotlin | `build.gradle.kts` |
| Version catalog | `gradle/libs.versions.toml` |

### Scope enforcement
Only edit paths matching `allowed_file_globs`. Do not modify application source (`src/main/java/**`) for pure dependency CVE tickets unless ticket explicitly requires code adaptation.

## examples

### Maven transitive override
```xml
<dependencyManagement>
  <dependencies>
    <dependency>
      <groupId>org.apache.commons</groupId>
      <artifactId>commons-text</artifactId>
      <version>1.10.0</version>
    </dependency>
  </dependencies>
</dependencyManagement>
```

### Gradle version catalog
```toml
[versions]
log4j = "2.17.1"

[libraries]
log4j-core = { module = "org.apache.logging.log4j:log4j-core", version.ref = "log4j" }
```

## test_cases

- **cve_direct_bump**: Direct dependency version raised to patched advisory minimum
- **transitive_constraint**: Transitive-only CVE resolved via BOM or `dependencyManagement`
- **catalog_alias_bump**: `libs.versions.toml` alias updated for vulnerable library

## allowed_file_globs

- `pom.xml`
- `build.gradle`
- `build.gradle.kts`
- `gradle/libs.versions.toml`
- `gradle.lockfile`
- `settings.gradle*`
- `!**/target/**`

## completion_criteria

| Phase | Done when |
|-------|-----------|
| Research | Vulnerable coordinate + manifest path + safe target version |
| Planner | EDIT steps only on allowed manifest globs |
| Implement | VERIFY shows bumped version; lockfile updated if applicable |
