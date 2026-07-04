"""Deterministic evaluators — routing, research, plan, and implement scoring."""

from __future__ import annotations

from typing import TYPE_CHECKING

from evals.evaluators.base import EvalScore, path_tail

if TYPE_CHECKING:
    from evals.harness.prediction import Prediction
    from evals.schema.case import EvalCase


def route_correctness(case: "EvalCase", pred: "Prediction") -> EvalScore:
    """Route Correctness for this module."""
    if "routing" not in case.eval_scope:
        return EvalScore("route_correct", 0.0, "not in scope", applicable=False)
    if pred.route is None:
        return EvalScore("route_correct", 0.0, f"no route (error: {pred.error})")
    ok = pred.route == case.expected_route
    return EvalScore(
        "route_correct",
        1.0 if ok else 0.0,
        f"expected={case.expected_route} actual={pred.route}",
    )


def file_identification_f1(case: "EvalCase", pred: "Prediction") -> EvalScore:
    """File Identification F1 for this module."""
    if "research" not in case.eval_scope:
        return EvalScore("file_f1", 0.0, "not in scope", applicable=False)

    expected = {path_tail(f) for f in case.expected_files if f}
    actual = {path_tail(f) for f in pred.files if f}

    if not expected:
        return EvalScore("file_f1", 1.0, "no expected files (vacuous)", applicable=False)

    if pred.error and not actual:
        return EvalScore("file_f1", 0.0, f"research errored: {pred.error[:120]}")

    tp = len(expected & actual)
    precision = tp / len(actual) if actual else 0.0
    recall = tp / len(expected)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0

    missed = sorted(expected - actual)
    comment = f"P={precision:.2f} R={recall:.2f} tp={tp}/{len(expected)}"
    if missed:
        comment += f" missed={missed}"
    return EvalScore("file_f1", round(f1, 4), comment)


_SYNONYMS = {
    "parameterize_query": "parameterize_sql",
    "prepared_statement": "parameterize_sql",
    "bcrypt": "secrets_removal",
    "hash_password": "secrets_removal",
    "argon2": "secrets_removal",
    "output_encoding": "xss_encoding",
    "html_encode": "xss_encoding",
    "authorization_check": "auth_check",
    "ownership_check": "auth_check",
    "upgrade_dependency": "dependency_upgrade",
}


def _canon(s: str) -> str:
    """Canon for this module."""
    s = (s or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _SYNONYMS.get(s, s)


def fix_type_match(case: "EvalCase", pred: "Prediction") -> EvalScore:
    """Fix Type Match for this module."""
    if "plan" not in case.eval_scope:
        return EvalScore("fix_type_match", 0.0, "not in scope", applicable=False)
    if not case.expected_fix_type:
        return EvalScore("fix_type_match", 1.0, "no expected fix type", applicable=False)

    expected = _canon(case.expected_fix_type)
    actual = _canon(pred.plan_fix_type)
    if not actual:
        return EvalScore("fix_type_match", 0.0, f"no plan fix type (error: {pred.error})")
    ok = expected == actual
    return EvalScore(
        "fix_type_match",
        1.0 if ok else 0.0,
        f"expected={expected} actual={actual}",
    )


def _overlap_ratio(exp_start: int, exp_end: int, act_start: int, act_end: int) -> float:
    """Overlap Ratio for this module."""
    if act_start <= 0 or act_end <= 0:
        return 0.0
    inter_start = max(exp_start, act_start)
    inter_end = min(exp_end, act_end)
    if inter_end < inter_start:
        return 0.0
    exp_len = max(exp_end - exp_start + 1, 1)
    return (inter_end - inter_start + 1) / exp_len


def plan_line_overlap(case: "EvalCase", pred: "Prediction") -> EvalScore:
    """Plan Line Overlap for this module."""
    if "plan" not in case.eval_scope:
        return EvalScore("plan_line_overlap", 0.0, "not in scope", applicable=False)

    expected = case.expected_line_ranges
    if not expected:
        return EvalScore("plan_line_overlap", 1.0, "no expected ranges (vacuous)", applicable=False)

    if pred.error and not pred.plan_line_ranges:
        return EvalScore("plan_line_overlap", 0.0, f"plan errored: {pred.error[:120]}")

    actual_by_file: dict[str, list[dict]] = {}
    for item in pred.plan_line_ranges:
        start = int(item.get("start", 0) or 0)
        end = int(item.get("end", 0) or 0)
        if start <= 0 or end <= 0:
            continue
        f = path_tail(str(item.get("file", "")))
        if f:
            actual_by_file.setdefault(f, []).append(item)

    scores: list[float] = []
    missing: list[str] = []
    for exp in expected:
        key = path_tail(exp.file)
        candidates = actual_by_file.get(key, [])
        if not candidates:
            missing.append(key)
            scores.append(0.0)
            continue
        best = max(
            _overlap_ratio(exp.start, exp.end, int(c.get("start", 0)), int(c.get("end", 0)))
            for c in candidates
        )
        scores.append(best)

    mean = sum(scores) / len(scores) if scores else 0.0
    comment = f"mean_overlap={mean:.2f} over {len(expected)} range(s)"
    if missing:
        comment += f"; no plan range for file(s)={missing}"
    return EvalScore("plan_line_overlap", round(mean, 4), comment)


def _added_content(patch: str) -> str:
    """Return only the post-fix code.

    If `patch` looks like a unified diff, keep added lines (`+`, not `+++`) so
    forbidden patterns sitting in REMOVED (`-`) lines don't cause false leaks.
    Non-diff text (e.g. a raw file body) is returned unchanged.
    """
    lines = patch.splitlines()
    is_diff = any(ln.startswith("+") or ln.startswith("-") for ln in lines)
    if not is_diff:
        return patch
    added = [ln[1:] for ln in lines if ln.startswith("+") and not ln.startswith("+++")]
    return "\n".join(added)


def patch_contains_excludes(case: "EvalCase", pred: "Prediction") -> EvalScore:
    """Patch Contains Excludes for this module."""
    if "implement" not in case.eval_scope:
        return EvalScore("patch_contains", 0.0, "not in scope", applicable=False)

    must = case.expected_patch_contains
    forbid = case.expected_patch_excludes
    if not must and not forbid:
        return EvalScore("patch_contains", 1.0, "no patch assertions", applicable=False)

    if not (pred.patch_text or ""):
        return EvalScore("patch_contains", 0.0, f"no patch text (error: {pred.error})")

    patch = _added_content(pred.patch_text)
    present = [s for s in must if s in patch]
    leaked = [s for s in forbid if s in patch]

    contains_score = (len(present) / len(must)) if must else 1.0
    forbidden_ok = not leaked
    score = contains_score if forbidden_ok else min(contains_score, 0.5)

    comment = f"contains {len(present)}/{len(must)}"
    if leaked:
        comment += f"; LEAKED forbidden={leaked}"
    missing = [s for s in must if s not in patch]
    if missing:
        comment += f"; missing={missing}"
    return EvalScore("patch_contains", round(score, 4), comment)
