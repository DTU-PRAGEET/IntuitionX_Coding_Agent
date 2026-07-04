"""Run an eval experiment and emit a scorecard.

Modes (Milestone 2.3 sub-agent unit evals):
  score      offline — score cases against recorded predictions (no LLM/Cursor).
  routing    live — orchestrator routing only.
  research   live — research agent only (file identification).
  plan       live — routing + research + planner.
  implement  live — routing + research + planner + executor (dry_run default).
  e2e        live — full pipeline (implement with dry_run=False).

Examples:
  python -m evals.runners.run_eval --mode score \\
      --cases evals/golden/unit_test --predictions evals/fixtures/predictions.json \\
      --out evals/reports/latest
  python -m evals.runners.run_eval --mode research --cases evals/golden/unit_test/dvpwa.json
  python -m evals.runners.run_eval --mode score --gate
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.evaluators import EVALUATORS
from evals.gates import all_gates_passed, format_gate_report, gates_from_scorecard
from evals.harness.prediction import Prediction, load_predictions
from evals.harness.targets import MODE_TO_SCOPE, TARGETS
from evals.report import CaseResult, Scorecard, build_scorecard, write_html, write_json
from evals.schema.case import EvalCase, load_cases

# Unit-eval modes only score evaluators for stages that actually ran.
# Without this, e.g. --mode research fails every case because plan/implement never run.
MODE_ACTIVE_SCOPES: dict[str, frozenset[str] | None] = {
    "routing": frozenset({"routing"}),
    "research": frozenset({"research"}),
    "plan": frozenset({"routing", "research", "plan"}),
    "implement": frozenset({"routing", "research", "plan", "implement"}),
    "e2e": None,
    "score": None,
}


def score_case(case: EvalCase, pred: Prediction, *, mode: str = "score") -> CaseResult:
    """Score Case for this module."""
    active = MODE_ACTIVE_SCOPES.get(mode)
    scores: dict[str, float] = {}
    comments: dict[str, str] = {}
    pass_flags: list[bool] = []
    for spec in EVALUATORS:
        if spec.scope not in case.eval_scope:
            continue
        if active is not None and spec.scope not in active:
            continue
        res = spec.fn(case, pred)
        if not res.applicable:
            continue
        scores[res.key] = res.score
        comments[res.key] = res.comment
        pass_flags.append(res.score >= spec.threshold)

    passed = bool(pass_flags) and all(pass_flags)
    return CaseResult(
        case_id=case.case_id,
        title=case.title,
        difficulty=case.difficulty,
        vuln_class=case.vuln_class or "unknown",
        expected_route=case.expected_route,
        passed=passed,
        scores=scores,
        comments=comments,
        error=pred.error,
        stage_failed=pred.stage_failed,
    )


def score_cases(
    cases: list[EvalCase],
    predictions: dict[str, Prediction],
    *,
    mode: str = "score",
) -> list[CaseResult]:
    """Score Cases for this module."""
    return [
        score_case(
            c,
            predictions.get(c.case_id) or Prediction(case_id=c.case_id, error="no prediction"),
            mode=mode,
        )
        for c in cases
    ]


def filter_cases_for_mode(cases: list[EvalCase], mode: str) -> list[EvalCase]:
    """Filter Cases For Mode for this module."""
    scope = MODE_TO_SCOPE.get(mode)
    if scope is None:
        filtered = cases
    else:
        filtered = [c for c in cases if scope in c.eval_scope]
    # Per-agent unit modes exclude integration cases (run those via e2e on integration_test/).
    if mode in ("routing", "research", "plan", "implement"):
        filtered = [c for c in filtered if c.case_kind == "unit"]
    return filtered


def predict_case(
    case: EvalCase,
    mode: str,
    fixtures: dict[str, Prediction],
    *,
    dry_run: bool = True,
) -> Prediction:
    """Predict Case for this module."""
    if mode == "score":
        pred = fixtures.get(case.case_id)
        if pred is None:
            return Prediction(case_id=case.case_id, error="no recorded prediction")
        return pred

    target = TARGETS.get(mode)
    if target is None:
        raise ValueError(f"unknown mode: {mode}")

    if mode in ("implement", "e2e"):
        return target(case, dry_run=dry_run if mode == "implement" else False)
    return target(case)


def run_experiment(
    *,
    mode: str = "score",
    cases_path: str = "evals/golden/unit_test",
    predictions_path: str = "evals/fixtures/predictions.json",
    out_prefix: str = "evals/reports/latest",
    experiment: str = "",
    dry_run: bool = True,
    check_gates: bool = False,
    fixtures_only: bool = False,
    export_predictions: str = "",
) -> tuple[Scorecard, list[CaseResult], int]:
    """Run predict + score + optional gate check. Returns (scorecard, results, exit_code)."""
    all_cases = load_cases(cases_path)
    if not all_cases:
        raise SystemExit(f"no cases found in {cases_path}")

    cases = filter_cases_for_mode(all_cases, mode)
    if not cases:
        raise SystemExit(f"no cases with scope for mode={mode!r} in {cases_path}")

    fixtures: dict[str, Prediction] = {}
    if mode == "score":
        pred_path = Path(predictions_path)
        if pred_path.exists():
            fixtures = load_predictions(pred_path)

    if mode == "score" and fixtures_only and fixtures:
        cases = [c for c in cases if c.case_id in fixtures]
        if not cases:
            raise SystemExit(f"no cases with recorded predictions in {predictions_path}")

    results: list[CaseResult] = []
    exported: list[dict] = []
    for c in cases:
        pred = predict_case(c, mode, fixtures, dry_run=dry_run)
        if export_predictions and mode != "score":
            exported.append(pred.model_dump(exclude_none=True))
        results.append(score_case(c, pred, mode=mode))

    if export_predictions and mode != "score":
        export_path = Path(export_predictions)
        export_path.parent.mkdir(parents=True, exist_ok=True)
        export_path.write_text(json.dumps(exported, indent=2) + "\n", encoding="utf-8")
        print(f"\n  exported predictions: {export_path} ({len(exported)} case(s))")

    label = experiment or f"week2-{mode}"
    scorecard = build_scorecard(label, mode, results)

    json_path = write_json(scorecard, f"{out_prefix}.json")
    html_path = write_html(scorecard, f"{out_prefix}.html")

    print(f"\n  {label}  ({mode})  cases={len(cases)}")
    print(f"  pass rate: {scorecard.passed}/{scorecard.total} = {scorecard.pass_rate:.0%}")
    for k, v in scorecard.evaluator_means.items():
        print(f"    {k:20s} mean={v}")
    for r in results:
        flag = "PASS" if r.passed else "FAIL"
        extra = ""
        if r.error:
            err = r.error
            if len(err) > 120:
                err = err[:117] + "..."
            extra = f"  [{r.stage_failed}: {err}]"
        print(f"    {flag}  {r.case_id}  {r.title}{extra}")
    print(f"\n  report: {json_path}\n          {html_path}")

    exit_code = 0
    if check_gates:
        gates = gates_from_scorecard(scorecard, all_cases)
        print(f"\n{format_gate_report(gates)}")
        if not all_gates_passed(gates):
            exit_code = 1

    return scorecard, results, exit_code


def run_unit_eval(mode: str) -> int:
    """CLI entry for per-agent unit eval modes (routing, research, plan, implement, e2e)."""
    ap = argparse.ArgumentParser(description=f"Run {mode} unit eval (Milestone 2.3).")
    ap.add_argument("--cases", default="evals/golden/unit_test")
    ap.add_argument("--out", default=f"evals/reports/{mode}")
    ap.add_argument("--experiment", default="")
    ap.add_argument("--no-dry-run", action="store_true")
    ap.add_argument("--gate", action="store_true")
    args = ap.parse_args()

    if mode == "e2e":
        dry_run = False
    elif mode == "implement":
        dry_run = True
    else:
        dry_run = True
    if args.no_dry_run:
        dry_run = False
    _, _, code = run_experiment(
        mode=mode,
        cases_path=args.cases,
        out_prefix=args.out,
        experiment=args.experiment or f"week2-{mode}",
        dry_run=dry_run,
        check_gates=args.gate,
    )
    return code


def main() -> int:
    """CLI entry point for this module."""
    ap = argparse.ArgumentParser(description="Run an eval experiment and emit a scorecard.")
    ap.add_argument(
        "--mode", default="score",
        choices=["score", "routing", "research", "plan", "implement", "e2e"],
    )
    ap.add_argument("--cases", default="evals/golden/unit_test", help="dir or single case file")
    ap.add_argument("--predictions", default="evals/fixtures/predictions.json",
                    help="dir/file of recorded predictions (mode=score)")
    ap.add_argument("--out", default="evals/reports/latest", help="output path prefix (no extension)")
    ap.add_argument("--experiment", default="", help="experiment label")
    ap.add_argument("--dry-run", action="store_true", default=False,
                    help="executor dry_run for implement mode (default True for implement)")
    ap.add_argument("--no-dry-run", action="store_true",
                    help="executor applies real patches (implement/e2e)")
    ap.add_argument("--gate", action="store_true",
                    help="enforce Milestone 2.3 gates (exit 1 on failure)")
    ap.add_argument("--fixtures-only", action="store_true",
                    help="score mode: only cases with a recorded prediction file")
    ap.add_argument("--export-predictions", default="",
                    help="live modes: write agent outputs to this JSON file for offline rescore")
    args = ap.parse_args()

    dry_run = True
    if args.mode == "e2e":
        dry_run = not args.no_dry_run
    elif args.mode == "implement":
        dry_run = not args.no_dry_run

    _, _, exit_code = run_experiment(
        mode=args.mode,
        cases_path=args.cases,
        predictions_path=args.predictions,
        out_prefix=args.out,
        experiment=args.experiment,
        dry_run=dry_run,
        check_gates=args.gate,
        fixtures_only=args.fixtures_only,
        export_predictions=args.export_predictions,
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
