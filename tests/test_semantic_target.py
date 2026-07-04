"""Tests for semantic target resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.core.schemas import ExecutionPlan, ExecutionStep
from src.core.semantic_target import resolve_semantic_target, validate_semantic_edits


def _write(tmp_path: Path, rel: str, content: str) -> str:
    fp = tmp_path / rel
    fp.parent.mkdir(parents=True, exist_ok=True)
    fp.write_text(content, encoding="utf-8")
    return rel


def _edit_step(**overrides) -> ExecutionStep:
    base = dict(
        step_id=1,
        action="EDIT",
        file="",
        target="fix",
        reason="r",
        validate="v",
        instruction="fix",
        intent_marker="FIXED",
    )
    base.update(overrides)
    return ExecutionStep(**base)


def test_resolve_function_target(tmp_path):
    rel = _write(
        tmp_path,
        "auth.py",
        "def validate_user(token):\n    return token\n\ndef other():\n    pass\n",
    )
    step = _edit_step(
        file=rel,
        target_type="function",
        target_name="validate_user",
    )
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.start_line == 1
    assert resolved.match_count == 1
    assert "validate_user" in resolved.snippet


def test_resolve_decorated_function(tmp_path):
    rel = _write(
        tmp_path,
        "views.py",
        "@login_required\n@csrf_exempt\ndef dashboard(request):\n    return None\n",
    )
    step = _edit_step(file=rel, target_type="function", target_name="dashboard")
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.match_count == 1
    assert resolved.start_line == 3


def test_resolve_method_with_parent(tmp_path):
    rel = _write(
        tmp_path,
        "svc.py",
        "class UserService:\n    def login(self):\n        pass\n\ndef login():\n    pass\n",
    )
    step = _edit_step(
        file=rel,
        target_type="method",
        target_name="login",
        parent_name="UserService",
    )
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.match_count == 1
    assert resolved.start_line == 2


def test_resolve_java_field_declaration(tmp_path):
    rel = _write(
        tmp_path,
        "UserService.java",
        """@Controller
public class UserService {
    private String apiKey;
    public void login() {}
}
""",
    )
    step = _edit_step(
        file=rel,
        target_type="variable",
        target_name="apiKey",
        parent_name="UserService",
    )
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.match_count == 1
    assert "apiKey" in resolved.snippet


def test_resolve_java_overloaded_methods_fail_validation(tmp_path):
    rel = _write(
        tmp_path,
        "Auth.java",
        """public class Auth {
    public void login(String user) {}
    public void login(int id) {}
}
""",
    )
    plan = ExecutionPlan(
        ticket_id="T",
        root_hypothesis="h",
        files_to_change=[rel],
        execution_steps=[
            _edit_step(
                file=rel,
                target_type="method",
                target_name="login",
                parent_name="Auth",
            ),
        ],
        risk_level="low",
    )
    hard, _soft = validate_semantic_edits(plan, str(tmp_path))
    assert any("found 2 matches" in e for e in hard)


def test_resolve_assignment_with_line_hint(tmp_path):
    rel = _write(
        tmp_path,
        "app.py",
        "x = 1\nsql = f'SELECT {q}'\ny = 2\n",
    )
    step = _edit_step(
        file=rel,
        target_type="assignment",
        target_name="sql",
        approximate_line=2,
    )
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.start_line == 2
    assert resolved.match_count == 1


def test_resolve_block_target(tmp_path):
    rel = _write(tmp_path, "cfg.py", "line1\nline2\nline3\n")
    step = _edit_step(
        file=rel,
        target_type="block",
        approximate_line=2,
        end_line=3,
    )
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.start_line == 2
    assert resolved.end_line == 3


def test_validate_unique_target_fails_on_duplicates(tmp_path):
    rel = _write(
        tmp_path,
        "dup.py",
        "def foo():\n    pass\n\ndef foo():\n    pass\n",
    )
    plan = ExecutionPlan(
        ticket_id="T",
        root_hypothesis="h",
        files_to_change=[rel],
        execution_steps=[
            _edit_step(file=rel, target_type="function", target_name="foo"),
        ],
        risk_level="low",
    )
    hard, _soft = validate_semantic_edits(plan, str(tmp_path))
    assert any("found 2 matches" in e for e in hard)


def test_nested_class_method_requires_parent(tmp_path):
    rel = _write(
        tmp_path,
        "nested.py",
        "class Outer:\n    class Inner:\n        def work(self):\n            pass\n",
    )
    step = _edit_step(
        file=rel,
        target_type="method",
        target_name="work",
        parent_name="Inner",
    )
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.match_count == 1


def test_resolve_dockerfile_arg_assignment(tmp_path):
    rel = _write(
        tmp_path,
        "Dockerfile.web",
        "FROM alpine\nARG CTF_KEY=secret_value\nENV ctf_key=$CTF_KEY\n",
    )
    step = _edit_step(
        file=rel,
        target_type="assignment",
        target_name="CTF_KEY",
        approximate_line=2,
        intent_marker="PLACEHOLDER_",
    )
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.match_count == 1
    assert resolved.start_line == 2
    assert "CTF_KEY" in resolved.snippet


def test_validate_dockerfile_arg_from_wrongsecrets_cache():
    repo = Path(".cache/eval_repos/wrongsecrets@3f6156bf1220")
    if not (repo / "Dockerfile.web").is_file():
        pytest.skip("wrongsecrets eval cache not present")
    plan = ExecutionPlan(
        ticket_id="SPACE-129070",
        root_hypothesis="h",
        files_to_change=["Dockerfile.web"],
        execution_steps=[
            _edit_step(
                step_id=11,
                file="Dockerfile.web",
                target_type="assignment",
                target_name="CTF_KEY",
                approximate_line=13,
                intent_marker="PLACEHOLDER_",
            ),
        ],
        risk_level="high",
    )
    hard, _soft = validate_semantic_edits(plan, str(repo))
    assert hard == []


def test_resolve_yaml_secret_key_assignment(tmp_path):
    rel = _write(
        tmp_path,
        "k8s/secrets-secret.yml",
        "apiVersion: v1\ndata:\n  funnier: dGhpcyBpcyBhcGFzc3dvcmQ=\nkind: Secret\n",
    )
    step = _edit_step(
        file=rel,
        target_type="assignment",
        target_name="funnier",
        parent_name="data",
        intent_marker="PLACEHOLDER_",
    )
    resolved = resolve_semantic_target(str(tmp_path), step)
    assert resolved is not None
    assert resolved.match_count == 1
    assert resolved.start_line == 3


def test_validate_yaml_secret_from_wrongsecrets_cache():
    repo = Path(".cache/eval_repos/wrongsecrets@3f6156bf1220")
    if not (repo / "k8s/secrets-secret.yml").is_file():
        pytest.skip("wrongsecrets eval cache not present")
    plan = ExecutionPlan(
        ticket_id="SPACE-129070",
        root_hypothesis="h",
        files_to_change=["k8s/secrets-secret.yml"],
        execution_steps=[
            _edit_step(
                step_id=11,
                file="k8s/secrets-secret.yml",
                target_type="assignment",
                target_name="funnier",
                intent_marker="PLACEHOLDER_",
            ),
        ],
        risk_level="high",
    )
    hard, _soft = validate_semantic_edits(plan, str(repo))
    assert hard == []
