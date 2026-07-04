"""Tests for shared runner utilities."""

from __future__ import annotations

import json

import pytest
from langchain_core.messages import AIMessage

from src.core.runner_utils import llm_content, parse_llm_json
from src.core.schemas import ResearchReport


def test_llm_content_flattens_gemini_list_blocks():
    """Test Llm Content Flattens Gemini List Blocks for this module."""
    msg = AIMessage(
        content=[
            {
                "type": "text",
                "text": '```json\n{"ticket_id": "SCRUM-5", "research_summary": "ok"}\n```',
            }
        ]
    )
    text = llm_content(msg)
    assert text.startswith("```json")
    assert "SCRUM-5" in text
    assert not text.startswith("[{")


def test_llm_content_plain_string_unchanged():
    """Test Llm Content Plain String Unchanged for this module."""
    msg = AIMessage(content='{"ticket_id": "X"}')
    assert llm_content(msg) == '{"ticket_id": "X"}'


def test_parse_llm_json_from_fenced_gemini_block():
    """Test Parse Llm Json From Fenced Gemini Block for this module."""
    payload = {
        "ticket_id": "SCRUM-5",
        "research_summary": "SQLi in search_users",
        "hypotheses": [
            {
                "rank": 1,
                "hypothesis": "f-string SQL",
                "evidence_strength": "strong",
                "evidence": ["grep hit"],
            }
        ],
        "affected_files": [
            {
                "path": "src/pipeline/fixtures/user_search_controller_vulnerable.py",
                "reason": "vulnerable f-string",
                "evidence_strength": "strong",
            }
        ],
        "affected_modules": ["user_search"],
        "module_blast_radius": [],
        "architectural_risk": "high",
        "issue_type_assessment": "code_bug",
        "flags": [],
    }
    fenced = f"```json\n{json.dumps(payload)}\n```"
    report = parse_llm_json(fenced, ResearchReport)
    assert report.ticket_id == "SCRUM-5"
    assert report.affected_files[0].path.endswith("user_search_controller_vulnerable.py")


def test_parse_llm_json_rejects_non_string():
    """Test Parse Llm Json Rejects Non String for this module."""
    with pytest.raises(ValueError, match="expected str"):
        parse_llm_json(["not", "a", "string"], ResearchReport)  # type: ignore[arg-type]
