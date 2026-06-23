---
# general_patterns.md
# Sections: [Evidence Rules ~20 lines] [Hypothesis ~15 lines] [Confidence ~15 lines]
# Load when: default research (optional, injected by runner for non-security tickets if needed)
---

## Table of Contents
1. Evidence Rules
2. Hypothesis Management
3. Confidence Scale

---

## 1. Evidence Rules

Every important conclusion must be backed by repository evidence.
Never claim evidence you have not observed via tools.

Examples of valid evidence:
- line contains SQL string interpolation
- exception thrown at specific location
- API calls unsafe method

## 2. Hypothesis Management

Maintain at most 3 hypotheses. Each must include reasoning, supporting evidence, and strength.
Discard weak hypotheses once stronger evidence exists.

## 3. Confidence Scale

- **Strong:** root cause directly observed in code
- **Moderate:** evidence strongly suggests root cause
- **Weak:** insufficient evidence — document uncertainty in `flags`

Negative finding policy: if root cause not found, still emit ResearchReport with flags explaining what was searched.
