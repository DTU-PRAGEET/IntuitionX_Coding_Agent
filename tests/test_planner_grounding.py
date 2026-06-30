"""Tests for planner patch grounding from disk."""

from __future__ import annotations

from src.agents.planner.agent import (
    _affected_file_contents,
    _ground_edit_patches_from_disk,
    _grounding_candidate_paths,
)
from src.core.schemas import (
    AffectedFile,
    ExecutionPlan,
    ExecutionStep,
    Hypothesis,
    ResearchReport,
)


def _sample_report() -> ResearchReport:
    return ResearchReport(
        ticket_id="SPACE-129069",
        research_summary="Multiple issues in sqli app; see sqli/dao/user.py for weak hashing.",
        hypotheses=[
            Hypothesis(
                rank=1,
                hypothesis="XSS via disabled autoescape",
                evidence_strength="strong",
                evidence=["sqli/app.py setup_jinja autoescape=False"],
            ),
            Hypothesis(
                rank=2,
                hypothesis="SQL injection in student creation",
                evidence_strength="strong",
                evidence=["sqli/dao/student.py uses string interpolation in INSERT"],
            ),
            Hypothesis(
                rank=3,
                hypothesis="Session cookies missing HttpOnly",
                evidence_strength="moderate",
                evidence=["sqli/middlewares.py RedisStorage httponly=False"],
            ),
        ],
        affected_files=[
            AffectedFile(
                path="sqli/app.py",
                reason="Global Jinja2 autoescape disabled",
                evidence_strength="strong",
            )
        ],
        affected_modules=["sqli"],
        module_blast_radius=["templates"],
        architectural_risk="high",
        issue_type_assessment="code_bug",
        flags=[],
    )


def test_grounding_candidate_paths_includes_hypothesis_evidence_files(tmp_path):
    repo = tmp_path / "repo"
    for rel in (
        "sqli/app.py",
        "sqli/dao/student.py",
        "sqli/dao/user.py",
        "sqli/middlewares.py",
    ):
        full = repo / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(f"# {rel}\n", encoding="utf-8")

    report = _sample_report()
    paths = _grounding_candidate_paths(report, str(repo))
    assert paths == [
        "sqli/app.py",
        "sqli/dao/user.py",
        "sqli/dao/student.py",
        "sqli/middlewares.py",
    ]


def test_affected_file_contents_includes_all_candidate_files_not_only_first_three(tmp_path):
    repo = tmp_path / "repo"
    for rel in (
        "sqli/app.py",
        "sqli/dao/student.py",
        "sqli/dao/user.py",
        "sqli/middlewares.py",
    ):
        full = repo / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(f"# contents of {rel}\n" * 5, encoding="utf-8")

    block = _affected_file_contents(_sample_report(), str(repo))
    assert "### sqli/app.py" in block
    assert "### sqli/dao/student.py" in block
    assert "### sqli/dao/user.py" in block
    assert "### sqli/middlewares.py" in block
    assert "contents of sqli/dao/student.py" in block
    assert "[:4000]" not in block


def test_ground_edit_patches_aligns_old_string_newlines(tmp_path):
    target = tmp_path / "handler.py"
    content = 'def search_users(query: str) -> list[dict[str, Any]]:\n    sql = "SELECT 1"\n'
    target.write_text(content, encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="fix",
        files_to_change=["handler.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file="handler.py",
                target="fix",
                reason="fix",
                validate="patched",
                old_string=content + "\n",
                new_string='sql = "SELECT * FROM users WHERE name LIKE ?"\n',
            )
        ],
        risk_level="high",
    )
    grounded = _ground_edit_patches_from_disk(plan, str(tmp_path))
    edit = grounded.execution_steps[0]
    assert edit.old_string == content


def test_ground_truncated_patch_leaves_step_unchanged(tmp_path):
    target = tmp_path / "handler.py"
    content = (
        'def search_users(query: str) -> list[dict[str, Any]]:\n'
        '    sql = f"SELECT * FROM users WHERE name LIKE \'%{query}%\'"\n'
        '    return [{"sql": sql, "query": query}]\n'
    )
    target.write_text(content, encoding="utf-8")
    prefix_old = content[:80]
    plan = ExecutionPlan(
        ticket_id="PLAT-1234",
        root_hypothesis="fix",
        files_to_change=["handler.py"],
        execution_steps=[
            ExecutionStep(
                step_id=1,
                action="EDIT",
                file="handler.py",
                target="fix",
                reason="fix",
                validate="patched",
                old_string=prefix_old,
                new_string='sql = "SELECT * FROM users WHERE name LIKE ?"\n',
            )
        ],
        risk_level="high",
    )
    grounded = _ground_edit_patches_from_disk(plan, str(tmp_path))
    edit = grounded.execution_steps[0]
    assert edit.old_string == prefix_old
    assert edit.new_string == 'sql = "SELECT * FROM users WHERE name LIKE ?"\n'


def test_ground_aligns_trailing_newline_mismatch(tmp_path):
    target = tmp_path / "sqli/dao/student.py"
    target.parent.mkdir(parents=True)
    content = "vulnerable\n\n\n"
    target.write_text(content, encoding="utf-8")
    plan = ExecutionPlan(
        ticket_id="SPACE-129068",
        root_hypothesis="fix",
        files_to_change=["sqli/dao/student.py"],
        execution_steps=[
            ExecutionStep(
                step_id=3,
                action="EDIT",
                file="sqli/dao/student.py",
                target="fix",
                reason="fix",
                validate="patched",
                old_string=content + "\n",
                new_string="fixed\n\n\n",
                intent_marker="fixed",
            )
        ],
        risk_level="high",
    )
    grounded = _ground_edit_patches_from_disk(plan, str(tmp_path))
    edit = grounded.execution_steps[0]
    assert edit.old_string == content
