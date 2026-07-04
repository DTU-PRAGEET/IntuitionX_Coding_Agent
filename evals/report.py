"""Scorecard aggregation + JSON/HTML report (Milestone 2.1 + 2.4).

Takes per-case evaluator scores and produces:
  * per-evaluator mean score
  * per-case pass/fail (all in-scope evaluators >= threshold)
  * breakdown by difficulty and vuln_class
  * a self-contained HTML scorecard
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class CaseResult:
    """One eval case score rollup — per-evaluator scores, pass flag, stage_failed.
    Emitted by `runners/run_eval.py`; listed inside `report.Scorecard.cases`."""
    case_id: str
    title: str
    difficulty: str
    vuln_class: str
    expected_route: str
    passed: bool
    scores: dict[str, float] = field(default_factory=dict)
    comments: dict[str, str] = field(default_factory=dict)
    error: str | None = None
    stage_failed: str | None = None


@dataclass
class Scorecard:
    """Experiment-wide eval summary — pass rate, evaluator means, breakdowns, HTML export.
    Written by `evals/report.py` after `run_eval` completes a benchmark run."""
    experiment: str
    mode: str
    created_at: str
    total: int
    passed: int
    pass_rate: float
    evaluator_means: dict[str, float]
    by_difficulty: dict[str, dict[str, int]]
    by_vuln_class: dict[str, dict[str, int]]
    cases: list[CaseResult]

    def to_dict(self) -> dict[str, Any]:
        """To Dict for Scorecard."""
        d = asdict(self)
        return d


def build_scorecard(experiment: str, mode: str, results: list[CaseResult]) -> Scorecard:
    """Build scorecard."""
    total = len(results)
    passed = sum(1 for r in results if r.passed)

    sums: dict[str, float] = {}
    counts: dict[str, int] = {}
    for r in results:
        for k, v in r.scores.items():
            sums[k] = sums.get(k, 0.0) + v
            counts[k] = counts.get(k, 0) + 1
    means = {k: round(sums[k] / counts[k], 4) for k in sums}

    def bucket(attr: str) -> dict[str, dict[str, int]]:
        """Bucket for this module."""
        out: dict[str, dict[str, int]] = {}
        for r in results:
            key = getattr(r, attr) or "unknown"
            b = out.setdefault(key, {"pass": 0, "fail": 0})
            b["pass" if r.passed else "fail"] += 1
        return out

    return Scorecard(
        experiment=experiment,
        mode=mode,
        created_at=datetime.now(timezone.utc).isoformat(),
        total=total,
        passed=passed,
        pass_rate=round(passed / total, 4) if total else 0.0,
        evaluator_means=means,
        by_difficulty=bucket("difficulty"),
        by_vuln_class=bucket("vuln_class"),
        cases=results,
    )


def write_json(scorecard: Scorecard, path: str | Path) -> Path:
    """Write Json for this module."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(scorecard.to_dict(), indent=2), encoding="utf-8")
    return p


def write_html(scorecard: Scorecard, path: str | Path) -> Path:
    """Write Html for this module."""
    sc = scorecard
    rows = []
    for c in sc.cases:
        badge = "PASS" if c.passed else "FAIL"
        color = "#1a7f37" if c.passed else "#cf222e"
        score_cells = " ".join(f"<code>{k}={v}</code>" for k, v in c.scores.items())
        rows.append(
            f"<tr><td>{c.case_id}</td><td>{c.title}</td>"
            f"<td>{c.difficulty}</td><td>{c.vuln_class}</td>"
            f"<td style='color:{color};font-weight:600'>{badge}</td>"
            f"<td>{score_cells}</td></tr>"
        )
    means = " ".join(f"<code>{k}={v}</code>" for k, v in sc.evaluator_means.items())
    html = f"""<!doctype html><html><head><meta charset="utf-8">
<title>Eval Scorecard — {sc.experiment}</title>
<style>body{{font-family:system-ui,sans-serif;margin:2rem;color:#1f2328}}
table{{border-collapse:collapse;width:100%;margin-top:1rem}}
th,td{{border:1px solid #d0d7de;padding:6px 10px;text-align:left;font-size:14px}}
th{{background:#f6f8fa}} code{{background:#f6f8fa;padding:1px 5px;border-radius:4px}}
.kpi{{font-size:1.4rem;font-weight:700}}</style></head><body>
<h1>Eval Scorecard</h1>
<p><b>Experiment:</b> {sc.experiment} &nbsp; <b>Mode:</b> {sc.mode} &nbsp; <b>When:</b> {sc.created_at}</p>
<p class="kpi">Pass rate: {sc.passed}/{sc.total} = {sc.pass_rate:.0%}</p>
<p><b>Evaluator means:</b> {means}</p>
<h2>By difficulty</h2><pre>{json.dumps(sc.by_difficulty, indent=2)}</pre>
<h2>By vuln class</h2><pre>{json.dumps(sc.by_vuln_class, indent=2)}</pre>
<h2>Cases</h2>
<table><tr><th>ID</th><th>Title</th><th>Difficulty</th><th>Vuln</th><th>Result</th><th>Scores</th></tr>
{''.join(rows)}
</table></body></html>"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(html, encoding="utf-8")
    return p
