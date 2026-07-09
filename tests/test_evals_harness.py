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
from evals.harness.targets import (
    _fill_plan_fields,
    _infer_fix_type,
    _is_micro_expected_route,
    _plan_line_ranges,
    _routing_repo_context,
)
from evals.harness.ticket_builder import build_jira_ticket
from evals.report import build_scorecard, write_html, write_json
from evals.runners.run_eval import filter_cases_for_mode, score_case
from evals.schema.case import load_cases, validate_dir
from src.core.orchestrator_context import build_orchestrator_routing_context
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


def test_routing_repo_context_only_for_micro_cases():
    """MICRO golden cases must supply repository.repo_path to the orchestrator."""
    cases = load_cases(GOLDEN)
    micro = [c for c in cases if _is_micro_expected_route(c)]
    assert micro, "expected at least one MICRO case in golden set"
    full = next(c for c in cases if c.expected_route == "FULL" and c.all_repos())

    full_repo, full_profile = _routing_repo_context(full)
    assert full_repo is None
    assert full_profile is None

    for case in micro:
        repo_path, profile = _routing_repo_context(case)
        assert repo_path, f"{case.case_id}: MICRO routing eval requires a resolved repo_path"
        assert Path(repo_path).is_dir(), f"{case.case_id}: repo_path must exist on disk"
        ctx = build_orchestrator_routing_context(
            build_jira_ticket(case),
            repo_path=repo_path,
            repo_profile=profile,
        )
        assert ctx["repository"]["repo_path"] == repo_path


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


def test_infer_fix_type_from_edit_instruction():
    """Test infer fix type from instruction text."""
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
                instruction="Use PreparedStatement with pstmt.setString for accountName parameterization.",
                intent_marker="PreparedStatement",
                target_type="method",
                target_name="injectableQuery",
            )
        ],
        risk_level="high",
    )
    assert _infer_fix_type(plan) == "parameterize_sql"


def test_plan_line_ranges_inferred_from_approximate_line():
    """Test plan line ranges from approximate_line hint."""
    repo = Path(WEBGOAT_REPO)
    if not (repo / WEBGOAT_FILE).is_file():
        return

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
                approximate_line=47,
                instruction="Parameterize SQL in injectableQuery using PreparedStatement.",
                intent_marker="PreparedStatement",
                target_type="method",
                target_name="injectableQuery",
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
                approximate_line=47,
                instruction="Use PreparedStatement with pstmt.setString(1, accountName) for SQL parameterization.",
                intent_marker="PreparedStatement",
                target_type="method",
                target_name="injectableQuery",
            )
        ],
        risk_level="high",
    )
    pred = Prediction(case_id="G-10", route="FULL", files=[WEBGOAT_FILE])
    _fill_plan_fields(pred, plan, repo_path=str(repo))

    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-10")
    result = score_case(case, pred, mode="plan", legacy=not pred.artifact_path)
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
    results = [score_case(c, fixtures[c.case_id], legacy=True) for c in cases if c.case_id in fixtures]
    sc = build_scorecard("gate-test", "score", results)
    gates = gates_from_scorecard(sc, cases, legacy=True)
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


def test_pipeline_artifact_roundtrip(tmp_path: Path):
    """PipelineArtifact saves and loads with hash."""
    from evals.artifacts.pipeline_result import (
        FileContext,
        PipelineArtifact,
        allocate_run_dir,
        load_artifact,
        save_artifact,
    )

    run_id, _ = allocate_run_dir(tmp_path / "runs")
    art = PipelineArtifact(
        case_id="G-01",
        run_id=run_id,
        captured_at="2026-01-01T00:00:00Z",
        ticket={"issue_id": "G-01"},
        plan={"execution_steps": [{"action": "EDIT", "file": "sqli/dao/student.py"}]},
        before_context=[FileContext(file="sqli/dao/student.py", function="create", code="line1")],
        after_context=[FileContext(file="sqli/dao/student.py", function="create", code="line2")],
    ).finalize()
    path = save_artifact(art, runs_root=tmp_path / "runs")
    loaded = load_artifact(run_id, "G-01", runs_root=tmp_path / "runs")
    assert loaded.case_id == "G-01"
    assert loaded.artifact_hash == art.artifact_hash
    assert path.exists()


def test_executor_preflight_validation_failure_not_fatal():
    """Edit validation failures are warnings — executor judge should still run."""
    from evals.artifacts.pipeline_result import PipelineArtifact
    from evals.evaluators.preflight import executor_preflight

    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    art = PipelineArtifact(
        case_id="G-01",
        run_id="run_test",
        captured_at="2026-01-01T00:00:00Z",
        plan={
            "files_to_change": ["sqli/dao/student.py"],
            "execution_steps": [
                {
                    "step_id": 1,
                    "action": "EDIT",
                    "file": "sqli/dao/student.py",
                    "instruction": "parameterize",
                    "target_type": "function",
                    "target_name": "create",
                    "intent_marker": "execute",
                }
            ],
        },
        git_diff="diff --git a/sqli/dao/student.py",
        changed_files=["sqli/dao/student.py"],
        executor_validations=[
            {
                "step_id": 1,
                "file": "sqli/dao/student.py",
                "passed": False,
                "confidence": 0.0,
                "reason": "validator disagreed with equivalent API",
            }
        ],
        error=(
            "Executor edit validation failed after 2 batch pass(es). "
            "1 step(s) still failing: - step 1"
        ),
        stage_failed="executor",
    )
    result = executor_preflight(case, art)
    assert not result.skip_judge
    assert not result.fatals
    assert any("validation failed" in w for w in result.warnings)


def test_planner_preflight_warning_not_fatal():
    """match_count=0 is a warning — judge should not be skipped."""
    from evals.artifacts.pipeline_result import PipelineArtifact
    from evals.evaluators.preflight import planner_preflight

    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    art = PipelineArtifact(
        case_id="G-01",
        run_id="run_test",
        captured_at="2026-01-01T00:00:00Z",
        plan={
            "files_to_change": ["sqli/dao/student.py"],
            "execution_steps": [
                {
                    "step_id": 1,
                    "action": "EDIT",
                    "file": "sqli/dao/student.py",
                    "instruction": "parameterize",
                    "target_type": "function",
                    "target_name": "create",
                    "intent_marker": "execute",
                }
            ],
        },
        plan_validation={
            "fatals": [],
            "warnings": ["EDIT step 1: missing approximate_line hint"],
            "edit_outcomes": [{"step_id": 1, "noop_diff": True, "passed": False}],
        },
    )
    result = planner_preflight(case, art)
    assert not result.skip_judge
    assert result.warnings


def test_planner_preflight_fatal_invalid_json():
    """Missing plan is fatal — skip judge."""
    from evals.artifacts.pipeline_result import PipelineArtifact
    from evals.evaluators.preflight import planner_preflight

    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    art = PipelineArtifact(
        case_id="G-01",
        run_id="run_test",
        captured_at="2026-01-01T00:00:00Z",
        plan=None,
        stage_failed="planner",
        error="Invalid JSON",
    )
    result = planner_preflight(case, art)
    assert result.skip_judge
    assert result.fatals


def test_judge_verdict_weighted_score():
    """JudgeVerdict computes weighted composite."""
    from evals.evaluators.llm_judge_base import EXECUTOR_CRITERIA_WEIGHTS, CriterionScore, JudgeVerdict

    verdict = JudgeVerdict(
        score=80,
        passed=True,
        criteria={
            "intent_implementation": CriterionScore(score=90, reason="ok"),
            "security_fix_quality": CriterionScore(score=85, reason="ok"),
            "code_correctness": CriterionScore(score=80, reason="ok"),
            "precision": CriterionScore(score=75, reason="ok"),
            "regression_risk": CriterionScore(score=70, reason="ok"),
            "validation_honesty": CriterionScore(score=85, reason="ok"),
            "response_accuracy": CriterionScore(score=80, reason="ok"),
        },
    )
    assert verdict.normalized(EXECUTOR_CRITERIA_WEIGHTS) > 0.7


def test_format_judge_comment_includes_full_reasoning_and_criteria():
    """LangSmith comment must not truncate judge reasoning or per-criterion detail."""
    from evals.evaluators.llm_judge_base import CriterionScore, JudgeVerdict, format_judge_comment

    long_reason = (
        "The executor largely followed the plan's intended remediation by replacing committed "
        "Secrets with SealedSecret manifests, but the post-edit excerpts show corrupted apiVersion "
        "lines that would prevent Kubernetes from accepting these resources."
    )
    criterion_reason = (
        "Post-edit manifests appear syntactically corrupted: the after-context shows invalid YAML "
        "which would prevent kubectl from applying these resources."
    )
    verdict = JudgeVerdict(
        score=35,
        passed=False,
        reasoning=long_reason,
        criteria={
            "code_correctness": CriterionScore(score=0, reason=criterion_reason),
        },
        hard_failures=["Final YAML appears invalid in multiple modified files."],
        soft_failures=["Repository artifacts are internally inconsistent."],
    )
    comment = format_judge_comment(verdict)
    assert long_reason in comment
    assert criterion_reason in comment
    assert "Hard failures:" in comment
    assert "Soft failures:" in comment
    assert "code_correctness: 0" in comment
    assert len(comment) > 240


def test_compare_runs(tmp_path: Path, monkeypatch):
    """compare_runs reports delta between two reports."""
    from evals.runners.compare_runs import compare_runs

    runs = tmp_path / "evals" / "runs"
    (runs / "run_a").mkdir(parents=True)
    (runs / "run_b").mkdir(parents=True)
    (runs / "run_a" / "report.json").write_text(
        '{"evaluator_means": {"planner_judge": 0.71}, "pass_rate": 0.5}',
        encoding="utf-8",
    )
    (runs / "run_b" / "report.json").write_text(
        '{"evaluator_means": {"planner_judge": 0.86}, "pass_rate": 0.8}',
        encoding="utf-8",
    )
    monkeypatch.chdir(tmp_path)
    text = compare_runs("run_a", "run_b")
    assert "planner_judge" in text
    assert "0.71" in text
    assert "0.86" in text


def test_implement_target_forwards_artifact_kwargs():
    """implement mode must accept run_id/save_artifact like integration."""
    import inspect

    from evals.harness.targets import run_implement_target

    params = inspect.signature(run_implement_target).parameters
    assert "run_id" in params
    assert "save_artifact" in params


def test_edit_outcomes_populate_executor_validations():
    """Artifact capture maps edit_outcome telemetry to executor_validations."""
    from evals.harness.artifact_capture import (
        build_pipeline_artifact,
        edit_outcomes_to_executor_validations,
    )

    outcomes = edit_outcomes_to_executor_validations(
        [
            {
                "step_id": 2,
                "file": "models/user.py",
                "passed": False,
                "noop_diff": True,
                "structural_fail": False,
                "validation_fail_reason": "no change applied",
                "retry_count": 1,
                "missing_requirements": ["use PreparedStatement"],
            }
        ]
    )
    assert len(outcomes) == 1
    assert outcomes[0]["confidence"] == 0.0
    assert outcomes[0]["noop_diff"] is True

    case = next(c for c in load_cases(GOLDEN) if c.case_id == "G-01")
    art = build_pipeline_artifact(
        case,
        run_id="run_test",
        repo_path=".",
        execution=type(
            "Result",
            (),
            {
                "changed_files": ["sqli/dao/student.py"],
                "step_results": [
                    {
                        "step_id": 1,
                        "action": "EDIT",
                        "file": "sqli/dao/student.py",
                        "ok": True,
                        "edit_outcome": {
                            "step_id": 1,
                            "file": "sqli/dao/student.py",
                            "passed": True,
                            "noop_diff": False,
                            "structural_fail": False,
                            "retry_count": 0,
                        },
                    }
                ],
                "model_dump": lambda self: {},
            },
        )(),
    )
    assert len(art.executor_validations) == 1
    assert art.executor_validations[0]["passed"] is True
    assert art.plan_validation["edit_outcomes"][0]["step_id"] == 1


def test_resolve_snapshot_path_resets_cached_checkout(tmp_path, monkeypatch):
    """Cached eval repos are reset to pinned SHA before reuse."""
    from evals.harness import repo as repo_mod
    from evals.schema.case import RepoSnapshot

    cache_root = tmp_path / "cache"
    monkeypatch.setattr(repo_mod, "CACHE_ROOT", cache_root)

    dest = cache_root / "WebGoat@f69f564fcc34"
    dest.mkdir(parents=True)
    (dest / ".git").mkdir()

    calls: list[list[str]] = []

    def fake_run(cmd, cwd=None):
        calls.append(cmd)

    monkeypatch.setattr(repo_mod, "_run", fake_run)

    snap = RepoSnapshot(
        slug="WebGoat",
        url="https://github.com/WebGoat/WebGoat",
        commit_sha="f69f564fcc34cf44aae273e06346fc6216b30e18",
    )
    path = repo_mod.resolve_snapshot_path("G-13", snap)
    assert path == str(dest)
    assert ["git", "fetch", "--depth", "1", "origin", snap.commit_sha] in calls
    assert ["git", "reset", "--hard", snap.commit_sha] in calls
    assert ["git", "clean", "-fd"] in calls
