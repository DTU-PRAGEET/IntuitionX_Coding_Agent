"""Compare evaluator means between two eval runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def _load_report(run_id: str) -> dict:
    path = Path("evals/runs") / run_id / "report.json"
    if not path.is_file():
        alt = Path(f"evals/reports/{run_id}.json")
        if alt.is_file():
            path = alt
        else:
            raise SystemExit(f"no report for {run_id}: tried {path} and {alt}")
    return json.loads(path.read_text(encoding="utf-8"))


def compare_runs(run_a: str, run_b: str) -> str:
    """Return human-readable comparison of evaluator means."""
    a = _load_report(run_a)
    b = _load_report(run_b)
    keys = sorted(set(a.get("evaluator_means", {})) | set(b.get("evaluator_means", {})))
    lines = [f"Comparison: {run_a} → {run_b}", ""]
    for key in keys:
        va = a.get("evaluator_means", {}).get(key)
        vb = b.get("evaluator_means", {}).get(key)
        if va is None or vb is None:
            lines.append(f"  {key:22s}  {va} → {vb}")
            continue
        delta = vb - va
        sign = "+" if delta >= 0 else ""
        lines.append(f"  {key:22s}  {va:.4f} → {vb:.4f}  ({sign}{delta:.4f})")
    pa = a.get("pass_rate", 0)
    pb = b.get("pass_rate", 0)
    lines.append("")
    lines.append(f"  pass_rate  {pa:.0%} → {pb:.0%}")
    return "\n".join(lines)


def main() -> int:
    """CLI entry."""
    ap = argparse.ArgumentParser(description="Compare two eval run reports.")
    ap.add_argument("run_a", help="baseline run id, e.g. run_012")
    ap.add_argument("run_b", help="new run id, e.g. run_018")
    args = ap.parse_args()
    print(compare_runs(args.run_a, args.run_b))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
