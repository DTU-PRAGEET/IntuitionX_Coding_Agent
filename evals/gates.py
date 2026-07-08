"""Milestone pass gates — research (unchanged) + plan/implement hybrid judges.

Integration gates (artifact + LLM judge runs):
  plan       planner_preflight + planner_judge  ≥ thresholds
  implement  executor_preflight + executor_judge

Legacy gates (offline fixtures — patch/line evaluators):
  plan       fix_type_match + plan_line_overlap
  implement  patch_contains
"""

from __future__ import annotations

from dataclasses import dataclass

from evals.harness.prediction import Prediction
from evals.report import CaseResult, Scorecard
from evals.schema.case import EvalCase

MILESTONE_GATES: dict[str, dict[str, float]] = {
    "research": {"file_f1": 0.85},
    "plan": {"planner_judge": 0.75, "planner_preflight": 0.90},
    "implement": {"executor_judge": 0.75, "executor_preflight": 0.90},
}

LEGACY_MILESTONE_GATES: dict[str, dict[str, float]] = {
    "research": {"file_f1": 0.85},
    "plan": {"fix_type_match": 0.75, "plan_line_overlap": 0.75},
    "implement": {"patch_contains": 0.80},
}


@dataclass
class GateResult:
    """Milestone sub-agent gate check — scope evaluator mean vs threshold."""

    scope: str
    evaluator: str
    threshold: float
    actual: float
    case_count: int
    passed: bool

    @property
    def summary(self) -> str:
        """Summary for GateResult."""
        flag = "PASS" if self.passed else "FAIL"
        return (
            f"{flag}  {self.scope}/{self.evaluator}: "
            f"{self.actual:.1%} (need ≥{self.threshold:.0%}, n={self.case_count})"
        )


def _cases_for_scope(cases: list[EvalCase], scope: str) -> list[EvalCase]:
    """Cases For Scope for this module."""
    return [c for c in cases if scope in c.eval_scope]


def _mean_for_evaluator(results: list[CaseResult], key: str) -> tuple[float, int]:
    """Mean For Evaluator for this module."""
    vals = [r.scores[key] for r in results if key in r.scores]
    if not vals:
        return 0.0, 0
    return sum(vals) / len(vals), len(vals)


def evaluate_gates(
    cases: list[EvalCase],
    predictions: dict[str, Prediction],
    *,
    scopes: list[str] | None = None,
    legacy: bool = False,
) -> list[GateResult]:
    """Score cases and check milestone gates for the given scopes."""
    from evals.runners.run_eval import score_cases

    gates_map = LEGACY_MILESTONE_GATES if legacy else MILESTONE_GATES
    scopes = scopes or list(gates_map.keys())
    results = score_cases(cases, predictions, mode="score", legacy=legacy)
    by_id = {r.case_id: r for r in results}
    out: list[GateResult] = []

    for scope in scopes:
        thresholds = gates_map.get(scope, {})
        scoped_cases = _cases_for_scope(cases, scope)
        scoped_results = [by_id[c.case_id] for c in scoped_cases if c.case_id in by_id]

        for ev_key, threshold in thresholds.items():
            actual, n = _mean_for_evaluator(scoped_results, ev_key)
            out.append(GateResult(
                scope=scope,
                evaluator=ev_key,
                threshold=threshold,
                actual=actual,
                case_count=n,
                passed=n > 0 and actual >= threshold,
            ))
    return out


def gates_from_scorecard(
    scorecard: Scorecard,
    cases: list[EvalCase],
    *,
    legacy: bool = False,
) -> list[GateResult]:
    """Check gates using an existing scorecard's evaluator_means."""
    gates_map = LEGACY_MILESTONE_GATES if legacy else MILESTONE_GATES
    by_id = {c.case_id: c for c in scorecard.cases}
    out: list[GateResult] = []

    for scope, thresholds in gates_map.items():
        scoped_ids = {c.case_id for c in _cases_for_scope(cases, scope)}
        scoped_results = [by_id[cid] for cid in scoped_ids if cid in by_id]

        for ev_key, threshold in thresholds.items():
            actual, n = _mean_for_evaluator(scoped_results, ev_key)
            if n == 0 and ev_key in scorecard.evaluator_means:
                actual = scorecard.evaluator_means[ev_key]
                n = len(scoped_results)
            out.append(GateResult(
                scope=scope,
                evaluator=ev_key,
                threshold=threshold,
                actual=actual,
                case_count=n,
                passed=n > 0 and actual >= threshold,
            ))
    return out


def all_gates_passed(gates: list[GateResult]) -> bool:
    """All Gates Passed for this module."""
    return bool(gates) and all(g.passed for g in gates)


def format_gate_report(gates: list[GateResult]) -> str:
    """Format gate report."""
    lines = ["Milestone gates:"]
    for g in gates:
        lines.append(f"  {g.summary}")
    status = "ALL GATES PASS" if all_gates_passed(gates) else "GATE FAILURE"
    lines.append(f"  => {status}")
    return "\n".join(lines)
