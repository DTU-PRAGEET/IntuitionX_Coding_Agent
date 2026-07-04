"""LangSmith adapter — golden case resolution and output serialization."""

from __future__ import annotations

from evals.harness.prediction import Prediction
from evals.langsmith_adapter import (
    _case_from_langsmith_inputs,
    _pred_from_run,
    _prediction_to_langsmith_output,
    configure_langsmith_cases,
)
from evals.schema.case import load_cases

GOLDEN = "evals/golden/unit_test"


def test_langsmith_inputs_resolve_golden_case():
    """Test Langsmith Inputs Resolve Golden Case for this module."""
    configure_langsmith_cases(GOLDEN)
    g10 = next(c for c in load_cases(GOLDEN) if c.case_id == "G-10")
    case = _case_from_langsmith_inputs(
        {
            "jira_ticket": {"issue_id": "G-10"},
            "repo_snapshot": g10.repo.model_dump(),
        }
    )
    assert case.case_id == "G-10"
    assert case.expected_route == "FULL"
    assert case.expected_fix_type == "parameterize_sql"
    assert case.eval_scope == g10.eval_scope


def test_langsmith_inputs_resolve_g18_micro_route():
    """Test Langsmith Inputs Resolve G18 Micro Route for this module."""
    configure_langsmith_cases(GOLDEN)
    g18 = next(c for c in load_cases(GOLDEN) if c.case_id == "G-18")
    case = _case_from_langsmith_inputs(
        {
            "jira_ticket": {"issue_id": "G-18"},
            "repo_snapshot": g18.repo.model_dump(),
        }
    )
    assert case.expected_route == "MICRO"
    assert case.expected_fix_type == "secrets_removal"


def test_prediction_to_langsmith_output_keeps_empty_fields():
    """Test Prediction To Langsmith Output Keeps Empty Fields for this module."""
    pred = Prediction(case_id="G-10", route="FULL", plan_fix_type="parameterize_sql")
    out = _prediction_to_langsmith_output(pred)
    assert out["case_id"] == "G-10"
    assert "patch_text" in out
    assert out["patch_text"] == ""
    assert "plan_line_ranges" in out


def test_pred_from_run_unwraps_flat_outputs():
    """Test Pred From Run Unwraps Flat Outputs for this module."""
    class _Run:
        """Minimal LangSmith run stub — flat outputs dict for `_pred_from_run` tests."""
        outputs = {
            "case_id": "G-10",
            "route": "FULL",
            "plan_fix_type": "parameterize_sql",
            "plan_line_ranges": [{"file": "a.java", "start": 47, "end": 52}],
        }

    pred = _pred_from_run(_Run(), "G-10")
    assert pred.route == "FULL"
    assert pred.plan_fix_type == "parameterize_sql"
    assert pred.plan_line_ranges[0]["start"] == 47
