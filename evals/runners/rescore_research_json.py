"""Re-score saved research JSON outputs — no LLM, no Cursor.

Usage:
  python -m evals.runners.rescore_research_json \\
      --input evals/DOCS/Research_results.json \\
      --cases evals/golden \\
      --out evals/reports/research_rescore
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evals.harness.prediction import Prediction
from evals.report import build_scorecard, write_html, write_json
from evals.runners.run_eval import score_case
from evals.schema.case import EvalCase, load_cases


def _parse_research_blob(raw: object) -> dict | None:
    """Parse Research Blob for this module."""
    if isinstance(raw, dict):
        if raw.get("type") == "text" and "text" in raw:
            try:
                return json.loads(raw["text"])
            except json.JSONDecodeError:
                return None
        if "ticket_id" in raw:
            return raw
    return None


def load_research_predictions(path: Path) -> dict[str, Prediction]:
    """Load research predictions."""
    text = path.read_text(encoding="utf-8").strip()
    items: list[object] = []
    try:
        data = json.loads(text)
        items = data if isinstance(data, list) else [data]
    except json.JSONDecodeError:
        # JSONL or concatenated objects (one blob per line)
        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                items.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    out: dict[str, Prediction] = {}
    for item in items:
        if isinstance(item, list):
            for sub in item:
                report = _parse_research_blob(sub)
                if report:
                    _add_prediction(out, report)
        else:
            report = _parse_research_blob(item)
            if report:
                _add_prediction(out, report)
    return out


def _add_prediction(out: dict[str, Prediction], report: dict) -> None:
    """Add Prediction for this module."""
    cid = str(report.get("ticket_id", "")).strip()
    if not cid:
        return
    files = [
        str(f.get("path", "")).strip()
        for f in (report.get("affected_files") or [])
        if isinstance(f, dict) and f.get("path")
    ]
    out[cid] = Prediction(
        case_id=cid,
        files=files,
        research_summary=str(report.get("research_summary", "")),
    )


def main() -> int:
    """CLI entry point for this module."""
    ap = argparse.ArgumentParser(description="Score saved research JSON (offline, free).")
    ap.add_argument("--input", required=True, help="Research_results.json path")
    ap.add_argument("--cases", default="evals/golden/unit_test")
    ap.add_argument("--out", default="evals/reports/research_rescore")
    args = ap.parse_args()

    cases = [c for c in load_cases(args.cases) if "research" in c.eval_scope]
    preds = load_research_predictions(Path(args.input))
    if not preds:
        raise SystemExit(f"no predictions parsed from {args.input}")

    results = []
    for case in cases:
        pred = preds.get(case.case_id) or Prediction(
            case_id=case.case_id, error="no saved research output"
        )
        results.append(score_case(case, pred, mode="research"))

    scorecard = build_scorecard("research-rescore", "research", results)
    write_json(scorecard, f"{args.out}.json")
    write_html(scorecard, f"{args.out}.html")

    print(f"\n  research-rescore  (offline)  cases={len(cases)}  parsed={len(preds)}")
    print(f"  pass rate (file_f1 only): {scorecard.passed}/{scorecard.total} = {scorecard.pass_rate:.0%}")
    for k, v in scorecard.evaluator_means.items():
        print(f"    {k:20s} mean={v}")
    for r in results:
        flag = "PASS" if r.passed else "FAIL"
        f1 = r.scores.get("file_f1", 0)
        comment = r.comments.get("file_f1", "")
        print(f"    {flag}  {r.case_id}  file_f1={f1}  {comment}")
    print(f"\n  report: {args.out}.json\n          {args.out}.html")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
