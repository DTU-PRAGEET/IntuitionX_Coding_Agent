"""Run LangSmith experiments against the golden dataset.

Wraps langsmith.evaluate() with mode → target/evaluator mapping from
evals.langsmith_adapter. Use this for dataset-linked experiments in the
LangSmith UI; use run_eval.py for local scorecards and gate checks.

Examples:
  python -m evals.runners.run_langsmith --mode routing \\
      --dataset IntuitionX-Golden-20-v1 --experiment-prefix routing-smoke

  python -m evals.runners.run_langsmith --mode research \\
      --case-ids G-01 --experiment-prefix research-smoke-v1
"""

from __future__ import annotations

import argparse
import os
import sys


def _langsmith_api_key() -> str:
    """Langsmith Api Key for this module."""
    return os.environ.get("LANGCHAIN_API_KEY") or os.environ.get("LANGSMITH_API_KEY") or ""


def _parse_case_ids(raw: str) -> list[str]:
    """Parse Case Ids for this module."""
    if not raw.strip():
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def _load_dataset_examples(
    dataset_name: str,
    mode: str,
    case_ids: list[str] | None = None,
    cases_dir: str = "evals/golden/unit_test",
):
    """Load Dataset Examples for this module."""
    from langsmith import Client

    from evals.harness.targets import MODE_TO_SCOPE
    from evals.schema.case import load_cases

    client = Client()
    if not client.has_dataset(dataset_name=dataset_name):
        raise SystemExit(
            f"LangSmith dataset {dataset_name!r} not found. Run: make evals-sync"
        )

    ds = client.read_dataset(dataset_name=dataset_name)
    examples = list(client.list_examples(dataset_id=ds.id))

    scope = MODE_TO_SCOPE.get(mode)
    golden = load_cases(cases_dir)
    if scope is not None:
        golden = [c for c in golden if scope in c.eval_scope]
    if mode in ("routing", "research", "plan", "implement"):
        golden = [c for c in golden if c.case_kind == "unit"]
    # allowed_by_scope = {c.case_id for c in golden}
    #     examples = [
    #         ex
    #         for ex in examples
    #         if (ex.metadata or {}).get("case_id") in allowed_by_scope
    #     ]
    allowed_by_scope = {c.case_id for c in golden}
    examples = [
        ex
        for ex in examples
        if (ex.metadata or {}).get("case_id") in allowed_by_scope
    ]
    if case_ids:
        allowed = set(case_ids)
        examples = [
            ex for ex in examples if (ex.metadata or {}).get("case_id") in allowed
        ]
        if not examples:
            raise SystemExit(
                f"no examples matched case_ids={sorted(allowed)} in dataset {dataset_name!r}"
            )
    if not examples:
        raise SystemExit(
            f"no examples in dataset {dataset_name!r} for mode={mode!r} "
            f"(check eval_scope in {cases_dir})"
        )
    return examples


def run_langsmith_experiment(
    *,
    mode: str,
    dataset: str,
    experiment_prefix: str,
    case_ids: list[str] | None = None,
    max_concurrency: int = 1,
    cases_dir: str = "evals/golden/unit_test",
):
    """Run langsmith experiment."""
    from langsmith.evaluation import evaluate

    from evals.langsmith_adapter import MODE_EVALUATORS, configure_langsmith_cases, make_langsmith_target

    if mode not in MODE_EVALUATORS:
        raise ValueError(f"unknown mode: {mode}")

    if not _langsmith_api_key():
        raise SystemExit(
            "LANGCHAIN_API_KEY is not set. Add it to .env in the project root "
            "(smith.langchain.com → Settings → API Keys)."
        )

    configure_langsmith_cases(cases_dir)
    target = make_langsmith_target(mode)
    evaluators = MODE_EVALUATORS[mode]
    examples = _load_dataset_examples(dataset, mode, case_ids, cases_dir=cases_dir)

    prefix = experiment_prefix or f"week2-{mode}"
    print(
        f"\nLangSmith experiment: mode={mode} dataset={dataset!r} "
        f"examples={len(examples)} prefix={prefix!r}\n"
    )
    if len(examples) > 1:
        print(
            f"  Running {len(examples)} case(s) sequentially (~2-5 min each for plan). "
            "Do not interrupt — LangSmith shows outputs only after each case finishes.\n",
            flush=True,
        )

    return evaluate(
        target,
        data=examples,
        evaluators=evaluators,
        experiment_prefix=prefix,
        max_concurrency=max_concurrency,
        metadata={"eval_mode": mode, "dataset": dataset},
    )


def main() -> int:
    """CLI entry point for this module."""
    ap = argparse.ArgumentParser(description="Run a LangSmith eval experiment.")
    ap.add_argument(
        "--mode",
        required=True,
        choices=["routing", "research", "plan", "implement", "e2e"],
        help="Agent stage to evaluate",
    )
    ap.add_argument(
        "--dataset",
        default="IntuitionX-Golden-20-v1",
        help="LangSmith dataset name (sync with make evals-sync)",
    )
    ap.add_argument(
        "--case-ids",
        default="",
        help="Comma-separated case IDs to run (e.g. G-01,G-02). Default: all examples.",
    )
    ap.add_argument(
        "--experiment-prefix",
        default="",
        help="Experiment name prefix in LangSmith UI",
    )
    ap.add_argument(
        "--cases-dir",
        default="evals/golden/unit_test",
        help="Golden cases directory for eval_scope / case_kind filtering",
    )
    ap.add_argument(
        "--max-concurrency",
        type=int,
        default=1,
        help="Parallel runs (keep at 1 to conserve Cursor/LLM budget)",
    )
    args = ap.parse_args()

    from src.core.config import load_project_env

    load_project_env()

    case_ids = _parse_case_ids(args.case_ids) or None
    prefix = args.experiment_prefix or f"week2-{args.mode}"

    try:
        results = run_langsmith_experiment(
            mode=args.mode,
            dataset=args.dataset,
            experiment_prefix=prefix,
            case_ids=case_ids,
            max_concurrency=args.max_concurrency,
            cases_dir=args.cases_dir,
        )
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130

    print(f"\nEvaluation complete. View results in LangSmith Experiments (prefix={prefix!r}).")
    if results is not None:
        print(f"Results: {results}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
