"""Tests for progressive validation context selection."""

from __future__ import annotations

import json

from src.agents.executor.validation import capture_pre_edit
from src.agents.executor.validation_context import (
    ContextLevel,
    diff_changed_line_ranges,
    find_semantic_scope,
    resolve_validation_snippets,
    semantic_scope_for_changed_lines,
    warrants_context_expansion,
)

_G18_ORIGINAL = """\
apiVersion: v1
data:
  funnier: dGhpcyBpcyBhcGFzc3dvcmQ=
kind: Secret
metadata:
  creationTimestamp: "2020-10-29T20:49:16Z"
  managedFields:
  - apiVersion: v1
    fieldsType: FieldsV1
    fieldsV1:
      f:data:
        .: {}
        f:funnier.entry: {}
      f:type: {}
    manager: kubectl-create
    operation: Update
    time: "2020-10-29T20:49:16Z"
  name: funnystuff
  namespace: default
  resourceVersion: "6559"
  selfLink: /api/v1/namespaces/default/secrets/funnystuff
  uid: baee7f4a-5161-4777-a512-3d236d3573d4
type: Opaque"""

_G18_MODIFIED = """\
apiVersion: bitnami.com/v1alpha1
kind: SealedSecret
metadata:
  name: funnystuff
  namespace: default
spec:
  encryptedData:
    funnier: AgBy3i4OJSWK+PiTySYZZA9rO43cGDEQAx"""


def _g18_step() -> dict:
    return {
        "step_id": 2,
        "action": "EDIT",
        "file": "k8s/secrets-secret.yml",
        "approximate_line": 1,
        "intent_marker": "SealedSecret",
        "target_type": "file",
        "target_name": "secrets-secret.yml",
        "instruction": "Replace the entire Secret manifest with a SealedSecret.",
    }

_G13_ORIGINAL = """\
  protected AttackResult execute(MultipartFile file, String fullName, String username) {
    if (file.isEmpty()) {
      return failed(this).feedback("path-traversal-profile-empty-file").build();
    }
    if (StringUtils.isEmpty(fullName)) {
      return failed(this).feedback("path-traversal-profile-empty-name").build();
    }

    File uploadDirectory = cleanupAndCreateDirectoryForUser(username);

    try {
      var uploadedFile = new File(uploadDirectory, fullName);
      uploadedFile.createNewFile();
      FileCopyUtils.copy(file.getBytes(), uploadedFile);

      if (attemptWasMade(uploadDirectory, uploadedFile)) {
        return solvedIt(uploadedFile);
      }
      return informationMessage(this)
          .feedback("path-traversal-profile-updated")
          .feedbackArgs(uploadedFile.getAbsoluteFile())
          .build();

    } catch (IOException e) {
      return failed(this).output(e.getMessage()).build();
    }
  }
"""

_G13_FIXED = """\
  protected AttackResult execute(MultipartFile file, String fullName, String username) {
    if (file.isEmpty()) {
      return failed(this).feedback("path-traversal-profile-empty-file").build();
    }
    if (StringUtils.isEmpty(fullName)) {
      return failed(this).feedback("path-traversal-profile-empty-name").build();
    }

    File uploadDirectory = cleanupAndCreateDirectoryForUser(username);

    try {
      String sanitizedFileName = FilenameUtils.getName(fullName);
      var uploadedFile = new File(uploadDirectory, sanitizedFileName);
      String uploadDirCanonical = uploadDirectory.getCanonicalPath();
      String uploadedFileCanonical = uploadedFile.getCanonicalPath();
      if (!uploadedFileCanonical.startsWith(uploadDirCanonical + File.separator)
          && !uploadedFileCanonical.equals(uploadDirCanonical)) {
        return failed(this).feedback("path-traversal-profile-invalid-path").build();
      }
      uploadedFile.createNewFile();
      FileCopyUtils.copy(file.getBytes(), uploadedFile);

      if (attemptWasMade(uploadDirectory, uploadedFile)) {
        return solvedIt(uploadedFile);
      }
      return informationMessage(this)
          .feedback("path-traversal-profile-updated")
          .feedbackArgs(uploadedFile.getAbsoluteFile())
          .build();

    } catch (IOException e) {
      return failed(this).output(e.getMessage()).build();
    }
  }
"""


def _g13_step() -> dict:
    return {
        "step_id": 2,
        "action": "EDIT",
        "file": "src/main/java/org/owasp/webgoat/lessons/pathtraversal/ProfileUploadBase.java",
        "approximate_line": 48,
        "intent_marker": "FilenameUtils.getName",
        "target_type": "method",
        "target_name": "execute",
        "instruction": "Add path traversal protection in execute method.",
    }


def test_diff_changed_line_ranges_detects_insertions():
    orig_changed, mod_changed = diff_changed_line_ranges("a\nb\nc\n", "a\nb\nX\nc\n")
    assert orig_changed == []
    assert mod_changed == [3]


def test_find_semantic_scope_java_method(tmp_path):
    rel = "ProfileUploadBase.java"
    target = tmp_path / rel
    target.write_text(_G13_ORIGINAL, encoding="utf-8")
    lines = _G13_ORIGINAL.splitlines()
    step = _g13_step()
    scope = find_semantic_scope(lines, 52, step, rel)
    assert scope is not None
    start, end = scope
    snippet = "\n".join(lines[start - 1 : end])
    assert "execute(" in snippet
    assert "new File(uploadDirectory, fullName)" in snippet
    assert "attemptWasMade" in snippet


def test_semantic_scope_for_changes_includes_canonical_path(tmp_path):
    rel = "ProfileUploadBase.java"
    orig_lines = _G13_ORIGINAL.splitlines()
    mod_lines = _G13_FIXED.splitlines()
    _, mod_changed = diff_changed_line_ranges("\n".join(orig_lines), "\n".join(mod_lines))
    scope = semantic_scope_for_changed_lines(mod_lines, mod_changed, _g13_step(), rel)
    assert scope is not None
    snippet = "\n".join(mod_lines[scope[0] - 1 : scope[1]])
    assert "FilenameUtils.getName" in snippet
    assert "getCanonicalPath" in snippet
    assert "attemptWasMade" in snippet


def test_capture_pre_edit_uses_method_scope_not_declaration_only(tmp_path):
    rel = "src/main/java/ProfileUploadBase.java"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_text(_G13_ORIGINAL, encoding="utf-8")
    pre = capture_pre_edit(str(tmp_path), _g13_step() | {"file": rel})
    assert "new File(uploadDirectory, fullName)" in pre.original_snippet
    assert pre.end_line - pre.start_line > 10


def test_warrants_context_expansion_skips_when_snippet_is_full_file():
    from src.agents.executor.validation import ValidationResult

    result = ValidationResult(
        passed=False,
        confidence=0.5,
        reason="missing fix",
        missing_requirements=["add check"],
    )
    assert not warrants_context_expansion(
        result,
        start_line=1,
        end_line=10,
        total_lines=10,
    )
    assert warrants_context_expansion(
        result,
        start_line=37,
        end_line=55,
        total_lines=140,
    )


def test_resolve_validation_snippets_diff_semantic_g13(tmp_path):
    rel = "src/main/java/ProfileUploadBase.java"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_text(_G13_FIXED, encoding="utf-8")
    step = _g13_step() | {"file": rel}
    from src.agents.executor.validation import PreEditCapture

    pre = PreEditCapture(
        original_snippet=_G13_ORIGINAL,
        start_line=1,
        end_line=len(_G13_ORIGINAL.splitlines()),
        full_file_content=_G13_ORIGINAL,
    )
    _, modified, start, end, level = resolve_validation_snippets(
        str(tmp_path),
        step,
        pre,
        level=ContextLevel.DIFF_SEMANTIC,
    )
    assert level == ContextLevel.DIFF_SEMANTIC
    assert "getCanonicalPath" in modified
    assert "attemptWasMade" in modified
    assert end - start > 15


def test_resolve_validation_snippets_k8s_manifest_full_file(tmp_path):
    """G-18: YAML manifest replacement must not produce empty modified snippet."""
    rel = "k8s/secrets-secret.yml"
    target = tmp_path / rel
    target.parent.mkdir(parents=True)
    target.write_text(_G18_MODIFIED, encoding="utf-8")
    step = _g18_step()
    from src.agents.executor.validation import PreEditCapture

    pre = PreEditCapture(
        original_snippet=_G18_ORIGINAL,
        start_line=1,
        end_line=len(_G18_ORIGINAL.splitlines()),
        full_file_content=_G18_ORIGINAL,
    )
    _, modified, start, end, level = resolve_validation_snippets(
        str(tmp_path),
        step,
        pre,
        level=ContextLevel.DIFF_SEMANTIC,
    )
    assert modified
    assert "SealedSecret" in modified
    assert "encryptedData" in modified
    assert start == 1
    assert end >= len(_G18_MODIFIED.splitlines())
