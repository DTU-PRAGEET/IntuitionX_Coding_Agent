"""Harness smoke tests (Milestone 2.1 DoD): schema + scoring + report, offline."""

from __future__ import annotations

import json
from pathlib import Path

from evals.evaluators import (
    file_identification_f1,
    fix_type_match,
    patch_contains_excludes,
    plan_line_overlap,
    route_correctness,
)
from evals.gates import all_gates_passed, gates_from_scorecard
from evals.harness.prediction import Prediction, load_predictions
from evals.harness.targets import _fill_plan_fields, _infer_fix_type, _plan_line_ranges
from evals.harness.ticket_builder import build_jira_ticket
from evals.report import build_scorecard, write_html, write_json
from evals.runners.run_eval import filter_cases_for_mode, score_case
from evals.schema.case import load_cases, validate_dir
from src.core.schemas import ExecutionPlan, ExecutionStep

GOLDEN = "evals/golden/unit_test"
PREDS = "evals/fixtures/predictions.json"
WEBGOAT_FILE = (
    "src/main/java/org/owasp/webgoat/lessons/sqlinjection/introduction/"
    "SqlInjectionLesson5a.java"
)
WEBGOAT_REPO = (
    ".cache/eval_repos/WebGoat@f69f564fcc34"
)


def test_golden_cases_validate():
    """Test Golden Cases Validate for this module."""
    count, _warns = validate_dir(GOLDEN)
    assert count >= 2  # G-00 dummy + G-01


def test_ticket_builder_produces_required_jira_fields():
    """Test Ticket Builder Produces Required Jira Fields for this module."""
    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    ticket = build_jira_ticket(case)
    for key in ("issue_id", "title", "status", "labels", "repo_links", "problem_statement"):
        assert key in ticket
    assert ticket["status"] == "In Progress"
    assert "sqli" in ticket["labels"]


def test_route_evaluator_exit_dummy():
    """Test Route Evaluator Exit Dummy for this module."""
    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-00")
    score = route_correctness(case, Prediction(case_id="G-00", route="EXIT"))
    assert score.score == 1.0
    bad = route_correctness(case, Prediction(case_id="G-00", route="FULL"))
    assert bad.score == 0.0


def test_file_f1_tolerant_paths():
    """Test File F1 Tolerant Paths for this module."""
    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    # leading "./" and different root should still match on the tail
    pred = Prediction(case_id="G-01", files=["./sqli/dao/student.py"])
    assert file_identification_f1(case, pred).score == 1.0


def test_patch_contains_detects_partial_fix():
    """Test Patch Contains Detects Partial Fix for this module."""
    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    # parameterized string present but params never bound -> forbidden pattern leaks
    partial = Prediction(
        case_id="G-01",
        patch_text="q = \"INSERT INTO students (name) VALUES ('%(name)s')\" % {'name': name}",
    )
    res = patch_contains_excludes(case, partial)
    assert res.score < 1.0


def test_fix_type_synonyms():
    """Test Fix Type Synonyms for this module."""
    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    pred = Prediction(case_id="G-01", plan_fix_type="parameterize_query")
    assert fix_type_match(case, pred).score == 1.0


def test_plan_line_overlap_perfect():
    """Test Plan Line Overlap Perfect for this module."""
    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    pred = Prediction(
        case_id="G-01",
        plan_line_ranges=[{"file": "sqli/dao/student.py", "start": 42, "end": 45}],
    )
    assert plan_line_overlap(case, pred).score == 1.0


def test_plan_line_overlap_ignores_zero_ranges():
    """Test Plan Line Overlap Ignores Zero Ranges for this module."""
    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    pred = Prediction(
        case_id="G-01",
        plan_line_ranges=[
            {"file": "sqli/dao/student.py", "start": 0, "end": 0},
            {"file": "sqli/dao/student.py", "start": 42, "end": 45},
        ],
    )
    assert plan_line_overlap(case, pred).score == 1.0


def test_infer_fix_type_from_edit_new_string():
    """Test Infer Fix Type From Edit New String for this module."""
    plan = ExecutionPlan(
        ticket_id="G-10",
        root_hypothesis="SQLi",
        files_to_change=[WEBGOAT_FILE],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file=WEBGOAT_FILE,
                target="method injectableQuery",
                reason="Replace concatenated SQL",
                validate="patched",
                new_string="PreparedStatement pstmt = connection.prepareStatement(query);\npstmt.setString(1, accountName);",
            )
        ],
        risk_level="high",
    )
    assert _infer_fix_type(plan) == "parameterize_sql"


def test_plan_line_ranges_inferred_from_old_string_on_disk():
    """Test Plan Line Ranges Inferred From Old String On Disk for this module."""
    repo = Path(WEBGOAT_REPO)
    if not (repo / WEBGOAT_FILE).is_file():
        return

    vulnerable = (
        "      query =\n"
        "          \"SELECT * FROM user_data WHERE first_name = 'John' and last_name = '\""
        " + accountName + \"'\";"
    )
    plan = ExecutionPlan(
        ticket_id="G-10",
        root_hypothesis="SQLi",
        files_to_change=[WEBGOAT_FILE],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file=WEBGOAT_FILE,
                target="injectableQuery",
                reason="parameterize",
                validate="patched",
                old_string=vulnerable,
                new_string="query = \"SELECT * FROM user_data WHERE first_name = 'John' and last_name = ?\";",
            )
        ],
        risk_level="high",
    )
    ranges = _plan_line_ranges(plan, repo_path=str(repo))
    assert len(ranges) == 1
    assert ranges[0]["start"] == 47
    assert ranges[0]["end"] >= 48


def test_fill_plan_fields_scores_g10_plan_mode():
    """Test Fill Plan Fields Scores G10 Plan Mode for this module."""
    repo = Path(WEBGOAT_REPO)
    if not (repo / WEBGOAT_FILE).is_file():
        return

    vulnerable = (
        "      query =\n"
        "          \"SELECT * FROM user_data WHERE first_name = 'John' and last_name = '\""
        " + accountName + \"'\";"
    )
    plan = ExecutionPlan(
        ticket_id="G-10",
        root_hypothesis="SQLi",
        files_to_change=[WEBGOAT_FILE],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file=WEBGOAT_FILE,
                target="method injectableQuery",
                reason="Bind account name via PreparedStatement",
                validate="patched",
                old_string=vulnerable,
                new_string="PreparedStatement pstmt = connection.prepareStatement(query);\npstmt.setString(1, accountName);",
            )
        ],
        risk_level="high",
    )
    pred = Prediction(case_id="G-10", route="FULL", files=[WEBGOAT_FILE])
    _fill_plan_fields(pred, plan, repo_path=str(repo))

    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-10")
    result = score_case(case, pred, mode="plan")
    assert pred.plan_fix_type == "parameterize_sql"
    assert pred.plan_line_ranges[0]["start"] > 0
    assert fix_type_match(case, pred).score == 1.0
    assert plan_line_overlap(case, pred).score >= 0.5
    assert result.passed is True


def test_integration_cases_validate():
    """Test Integration Cases Validate for this module."""
    count, _warns = validate_dir("evals/golden/integration_test")
    assert count == 5
    cases = load_cases("evals/golden/integration_test")
    assert all(c.case_kind == "integration" for c in cases)
    assert all(len(c.issues) >= 2 for c in cases)
    # One repo per case — all issues share the same repo slug
    for c in cases:
        slug = c.repo.slug
        assert slug
        assert all(i.repo_slug == slug for i in c.issues)


def test_filter_cases_for_research_mode():
    """Test Filter Cases For Research Mode for this module."""
    all_cases = load_cases("evals/golden")
    research_cases = filter_cases_for_mode(all_cases, "research")
    assert all("research" in c.eval_scope for c in research_cases)
    assert all(c.case_kind == "unit" for c in research_cases)
    assert any(c.case_id == "G-01" for c in research_cases)
    assert not any(c.case_id.startswith("I-") for c in research_cases)
    routing_only = filter_cases_for_mode(all_cases, "routing")
    assert all(c.case_id in {"G-00", "G-19", "G-20"} or "routing" in c.eval_scope for c in routing_only)


def test_milestone_gates_pass_on_fixtures():
    """Test Milestone Gates Pass On Fixtures for this module."""
    cases = load_cases(GOLDEN)
    fixtures = load_predictions(PREDS)
    results = [score_case(c, fixtures[c.case_id]) for c in cases if c.case_id in fixtures]
    sc = build_scorecard("gate-test", "score", results)
    gates = gates_from_scorecard(sc, cases)
    # G-00/G-01 fixtures are authored to pass their in-scope evaluators
    assert all_gates_passed(gates)


def test_offline_scorecard_end_to_end(tmp_path: Path):
    """Test Offline Scorecard End To End for this module."""
    cases = load_cases(GOLDEN)
    fixtures = load_predictions(PREDS)
    results = [score_case(c, fixtures.get(c.case_id) or Prediction(case_id=c.case_id, error="missing"))
               for c in cases]
    sc = build_scorecard("unit-smoke", "score", results)
    assert sc.total == len(cases)

    jp = write_json(sc, tmp_path / "r.json")
    hp = write_html(sc, tmp_path / "r.html")
    assert jp.exists() and hp.exists()
    data = json.loads(jp.read_text())
    assert "evaluator_means" in data and data["total"] == len(cases)
    # G-00 and G-01 fixtures are authored to pass
    by_id = {c.case_id: c for c in sc.cases}
    assert by_id["G-00"].passed is True
    assert by_id["G-01"].passed is True
