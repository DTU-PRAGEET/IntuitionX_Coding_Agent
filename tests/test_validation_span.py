"""Tests for edit validation span resolution."""

from __future__ import annotations

from src.agents.executor.validation import capture_pre_edit


def test_capture_pre_edit_ignores_approximate_line_one_when_anchors_exist(tmp_path):
    """approximate_line=1 must not restrict validation to file header when anchors exist later."""
    rel = "frontend/src/app/search-result/search-result.component.ts"
    target = tmp_path / rel
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        "/* header */\n"
        "import { DomSanitizer } from '@angular/platform-browser'\n"
        "\n"
        "export class SearchResultComponent {\n"
        "  filterTable () {\n"
        "    let queryParam: string = this.route.snapshot.queryParams.q\n"
        "    this.searchValue = this.sanitizer.bypassSecurityTrustHtml(queryParam)\n"
        "  }\n"
        "}\n",
        encoding="utf-8",
    )
    step = {
        "step_id": 2,
        "action": "EDIT",
        "file": rel,
        "approximate_line": 1,
        "intent_marker": "FIXED",
        "instruction": (
            "Remove DomSanitizer.bypassSecurityTrustHtml on attacker-controlled "
            "`q` query parameter data."
        ),
    }
    pre = capture_pre_edit(str(tmp_path), step)
    assert "bypassSecurityTrustHtml" in pre.original_snippet
    assert "queryParams" in pre.original_snippet
    assert "/* header */" not in pre.original_snippet or pre.end_line > 4
