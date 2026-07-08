"""Offline LLM judge scoring against saved pipeline artifacts."""

from __future__ import annotations

import argparse
import json

from evals.artifacts.pipeline_result import load_run_artifacts, load_run_metadata, run_dir
from evals.evaluators.executor_judge import executor_judge_from_artifact
from evals.evaluators.planner_judge import planner_judge_from_artifact
from evals.gates import all_gates_passed, format_gate_report, gates_from_scorecard
from evals.harness.prediction import Prediction
from evals.report import CaseResult, build_scorecard, write_html, write_json
from evals.runners.run_eval import score_case
from evals.schema.case import load_cases


def main() -> int:
    """Score artifacts in evals/runs/RUN_ID with LLM judges."""
    from src.core.config import load_project_env

    load_project_env()

    ap = argparse.ArgumentParser(description="Run LLM judges on saved pipeline artifacts.")
    ap.add_argument("--run-id", required=True, help="evals/runs run directory id, e.g. run_001")
    ap.add_argument("--cases", default="evals/golden/unit_test")
    ap.add_argument("--out", default="", help="report prefix (default: evals/runs/RUN_ID/report)")
    ap.add_argument("--judge-version", default="", help="override EVAL_JUDGE_VERSION for cache")
    ap.add_argument("--gate", action="store_true")
    ap.add_argument("--no-cache", action="store_true")
    args = ap.parse_args()

    artifacts = load_run_artifacts(args.run_id)
    if not artifacts:
        raise SystemExit(f"no artifacts in evals/runs/{args.run_id}/artifacts/")

    meta = load_run_metadata(args.run_id)
    cases_by_id = {c.case_id: c for c in load_cases(args.cases)}

    results: list[CaseResult] = []
    for case_id, artifact in sorted(artifacts.items()):
        case = cases_by_id.get(case_id)
        if case is None:
            continue
        pred = Prediction(
            case_id=case_id,
            artifact_path=str(run_dir(args.run_id) / "artifacts" / f"{case_id}.json"),
        )
        results.append(score_case(case, pred, mode="integration", run_judges=True))

    label = (meta.experiment if meta else "") or f"judge-{args.run_id}"
    scorecard = build_scorecard(label, "judge", results)

    out_prefix = args.out or str(run_dir(args.run_id) / "report")
    write_json(scorecard, f"{out_prefix}.json")
    write_html(scorecard, f"{out_prefix}.html")

    print(f"\n  judged {len(results)} case(s) from {args.run_id}")
    print(f"  pass rate: {scorecard.passed}/{scorecard.total} = {scorecard.pass_rate:.0%}")
    for k, v in scorecard.evaluator_means.items():
        print(f"    {k:22s} mean={v}")

    exit_code = 0
    if args.gate:
        all_cases = list(cases_by_id.values())
        gates = gates_from_scorecard(scorecard, all_cases, legacy=False)
        print(f"\n{format_gate_report(gates)}")
        if not all_gates_passed(gates):
            exit_code = 1

    summary = run_dir(args.run_id) / "report.json"
    summary.write_text(json.dumps(scorecard.to_dict(), indent=2) + "\n", encoding="utf-8")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
