"""Upload the git-versioned golden set to a LangSmith dataset.

The JSON files in evals/golden/ are the SOURCE OF TRUTH. This script mirrors them
into a LangSmith dataset so you can run experiments and compare across prompt
versions in the LangSmith UI. Re-running upserts (clears + re-adds) so the dataset
always matches the repo.

Usage:
  python -m evals.sync_langsmith --dataset IntuitionX-Golden-20-v1

Requires LANGCHAIN_API_KEY in the project root `.env` file.
"""

from __future__ import annotations

import argparse

from evals.harness.ticket_builder import build_jira_ticket
from evals.schema.case import EvalCase, load_cases


def _example_io(case: EvalCase) -> tuple[dict, dict, dict]:
    """Example Io for this module."""
    inputs = {
        "jira_ticket": build_jira_ticket(case),
        "repo_snapshot": case.repo.model_dump(),
    }
    outputs = {
        "case_id": case.case_id,
        "case_kind": case.case_kind,
        "expected_route": case.expected_route,
        "expected_files": case.expected_files,
        "expected_line_ranges": [r.model_dump() for r in case.expected_line_ranges],
        "expected_fix_type": case.expected_fix_type,
        "expected_patch_contains": case.expected_patch_contains,
        "expected_patch_excludes": case.expected_patch_excludes,
        "expected_vuln_class": case.vuln_class,
        "difficulty": case.difficulty,
        "pass_criteria": case.pass_criteria,
        "issues": [i.model_dump() for i in case.issues],
    }
    metadata = {
        "case_id": case.case_id,
        "case_kind": case.case_kind,
        "owasp_category": case.owasp_category,
        "cwe": case.cwe,
        "language": case.language,
        "difficulty": case.difficulty,
        "vuln_class": case.vuln_class,
        "expected_route": case.expected_route,
    }
    return inputs, outputs, metadata


def sync(dataset_name: str, cases_dir: str = "evals/golden/unit_test", description: str = "") -> None:
    """Sync for this module."""
    from langsmith import Client

    client = Client()
    cases = load_cases(cases_dir)
    if not cases:
        raise SystemExit(f"no cases in {cases_dir}")

    if client.has_dataset(dataset_name=dataset_name):
        ds = client.read_dataset(dataset_name=dataset_name)
        for ex in client.list_examples(dataset_id=ds.id):
            client.delete_example(example_id=ex.id)
    else:
        ds = client.create_dataset(
            dataset_name=dataset_name,
            description=description or f"IntuitionX golden set ({len(cases)} cases)",
        )

    for case in cases:
        inputs, outputs, metadata = _example_io(case)
        client.create_example(
            inputs=inputs, outputs=outputs, dataset_id=ds.id, metadata=metadata,
        )
    print(f"synced {len(cases)} example(s) -> LangSmith dataset '{dataset_name}'")


def main() -> int:
    """CLI entry point for this module."""
    ap = argparse.ArgumentParser(description="Sync golden cases to a LangSmith dataset.")
    ap.add_argument("--dataset", default="IntuitionX-Golden-20-v1")
    ap.add_argument("--cases", default="evals/golden/unit_test")
    ap.add_argument("--description", default="")
    args = ap.parse_args()

    from src.core.config import load_project_env

    load_project_env()
    sync(args.dataset, args.cases, args.description)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
