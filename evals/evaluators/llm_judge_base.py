"""Base LLM-as-Judge infrastructure — invoke, parse, cache, tiered models."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from src.core.runner_utils import invoke_tracked, llm_content, parse_llm_json

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
JUDGE_VERSION = os.getenv("EVAL_JUDGE_VERSION", "v1")

PLANNER_CRITERIA_WEIGHTS: dict[str, float] = {
    "root_cause_analysis": 0.20,
    "technical_correctness": 0.20,
    "file_selection": 0.15,
    "semantic_targeting": 0.15,
    "instruction_quality": 0.15,
    "executability": 0.10,
    "no_hallucination": 0.05,
}

EXECUTOR_CRITERIA_WEIGHTS: dict[str, float] = {
    "intent_implementation": 0.22,
    "security_fix_quality": 0.22,
    "code_correctness": 0.18,
    "precision": 0.13,
    "regression_risk": 0.10,
    "validation_honesty": 0.10,
    "response_accuracy": 0.05,
}


class CriterionScore(BaseModel):
    score: float = Field(ge=0, le=100)
    reason: str = ""


class JudgeVerdict(BaseModel):
    """Structured LLM judge output."""

    score: float = Field(ge=0, le=100)
    passed: bool = False
    criteria: dict[str, CriterionScore] = Field(default_factory=dict)
    hard_failures: list[str] = Field(default_factory=list)
    soft_failures: list[str] = Field(default_factory=list)
    reasoning: str = ""

    def weighted_score(self, weights: dict[str, float]) -> float:
        """Compute 0–100 composite from criteria weights."""
        if not self.criteria:
            return self.score
        total_w = 0.0
        acc = 0.0
        for key, w in weights.items():
            c = self.criteria.get(key)
            if c is not None:
                acc += c.score * w
                total_w += w
        if total_w <= 0:
            return self.score
        return round(acc / total_w, 2)

    def normalized(self, weights: dict[str, float]) -> float:
        """Return 0–1 score for EvalScore."""
        return round(self.weighted_score(weights) / 100.0, 4)


def format_judge_comment(verdict: JudgeVerdict, *, cached: bool = False) -> str:
    """Build LangSmith/scorecard comment with full reasoning and per-criterion detail."""
    lines: list[str] = []
    if cached:
        lines.append(f"cached score={verdict.score:.0f} passed={verdict.passed}")
    else:
        lines.append(f"score={verdict.score:.0f} passed={verdict.passed}")
    if verdict.reasoning:
        lines.extend(["", "Reasoning:", verdict.reasoning])
    if verdict.criteria:
        lines.append("")
        lines.append("Criteria:")
        for key, criterion in verdict.criteria.items():
            reason = criterion.reason.strip()
            if reason:
                lines.append(f"  {key}: {criterion.score:.0f} — {reason}")
            else:
                lines.append(f"  {key}: {criterion.score:.0f}")
    if verdict.hard_failures:
        lines.append("")
        lines.append("Hard failures:")
        lines.extend(f"  - {item}" for item in verdict.hard_failures)
    if verdict.soft_failures:
        lines.append("")
        lines.append("Soft failures:")
        lines.extend(f"  - {item}" for item in verdict.soft_failures)
    return "\n".join(lines)


def load_prompt(name: str) -> str:
    """Load prompt markdown from evals/prompts/."""
    path = PROMPTS_DIR / name
    return path.read_text(encoding="utf-8").strip()


def get_judge_llm():
    """Return LLM for judge based on EVAL_JUDGE_TIER env."""
    from src.core.config import load_project_env
    from src.core.llm import TaskComplexity, get_llm

    load_project_env()

    tier = os.getenv("EVAL_JUDGE_TIER", "dev").lower()
    if tier == "benchmark":
        prev = os.environ.get("LLM_PROVIDER")
        judge_provider = os.getenv("EVAL_JUDGE_PROVIDER") or os.getenv("LLM_PROVIDER", "sprinklr")
        os.environ["LLM_PROVIDER"] = judge_provider
        try:
            return get_llm(TaskComplexity.HIGH)
        finally:
            if prev is not None:
                os.environ["LLM_PROVIDER"] = prev
    return get_judge_llm_dev()


def tier_dev() -> bool:
    return os.getenv("EVAL_JUDGE_TIER", "dev").lower() != "benchmark"


def get_judge_llm_dev():
    """Cheap/fast model for prompt iteration."""
    from src.core.config import load_project_env

    load_project_env()
    provider = os.getenv("EVAL_JUDGE_PROVIDER") or os.getenv("LLM_PROVIDER", "sprinklr")
    model = os.getenv("EVAL_JUDGE_MODEL", "")

    if provider == "google" and not model:
        model = "gemini-2.5-flash"
    elif provider == "openai" and not model:
        model = "gpt-4o-mini"

    prev_provider = os.environ.get("LLM_PROVIDER")
    prev_google = os.environ.get("GOOGLE_MODEL")
    prev_openai = os.environ.get("OPENAI_MODEL")
    try:
        os.environ["LLM_PROVIDER"] = provider
        if model:
            if provider == "google":
                os.environ["GOOGLE_MODEL"] = model
            elif provider == "openai":
                os.environ["OPENAI_MODEL"] = model
            elif provider == "sprinklr":
                os.environ["SPRINKLR_LLM_MODEL"] = model
        from src.core.llm import TaskComplexity, get_llm

        return get_llm(TaskComplexity.LOW)
    finally:
        if prev_provider is not None:
            os.environ["LLM_PROVIDER"] = prev_provider
        elif "LLM_PROVIDER" in os.environ:
            del os.environ["LLM_PROVIDER"]
        if prev_google is not None:
            os.environ["GOOGLE_MODEL"] = prev_google
        if prev_openai is not None:
            os.environ["OPENAI_MODEL"] = prev_openai


def judge_cache_path(
    run_id: str,
    case_id: str,
    judge: str,
    *,
    judge_version: str = JUDGE_VERSION,
) -> Path:
    return Path("evals/runs") / run_id / "judge_results" / judge / f"{case_id}_{judge_version}.json"


def load_cached_verdict(
    run_id: str,
    case_id: str,
    judge: str,
    *,
    artifact_hash: str = "",
    judge_version: str = JUDGE_VERSION,
) -> JudgeVerdict | None:
    """Load cached judge verdict if hash matches."""
    path = judge_cache_path(run_id, case_id, judge, judge_version=judge_version)
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if artifact_hash and data.get("artifact_hash") != artifact_hash:
            return None
        return JudgeVerdict.model_validate(data["verdict"])
    except (OSError, json.JSONDecodeError, ValueError):
        return None


def save_cached_verdict(
    run_id: str,
    case_id: str,
    judge: str,
    verdict: JudgeVerdict,
    *,
    artifact_hash: str = "",
    judge_version: str = JUDGE_VERSION,
) -> Path:
    path = judge_cache_path(run_id, case_id, judge, judge_version=judge_version)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "artifact_hash": artifact_hash,
        "judge_version": judge_version,
        "verdict": verdict.model_dump(),
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def invoke_judge(system: str, user: str, *, llm=None) -> JudgeVerdict:
    """Call LLM judge and parse JSON verdict."""
    if llm is None:
        llm = get_judge_llm()
    from langchain_core.messages import HumanMessage, SystemMessage

    messages = [SystemMessage(content=system), HumanMessage(content=user)]
    response = invoke_tracked(llm, messages, agent="eval_judge")
    text = llm_content(response)
    return parse_llm_json(text, JudgeVerdict)


def format_file_contexts(contexts: list[Any]) -> str:
    """Format before/after FileContext list for prompts."""
    if not contexts:
        return "(none)"
    parts: list[str] = []
    for ctx in contexts:
        if hasattr(ctx, "model_dump"):
            d = ctx.model_dump()
        elif isinstance(ctx, dict):
            d = ctx
        else:
            continue
        fn = d.get("function") or "unknown"
        parts.append(f"### {d.get('file')} — {fn}\n```\n{d.get('code', '')}\n```")
    return "\n\n".join(parts)


def format_warnings(warnings: list[str]) -> str:
    if not warnings:
        return "(none)"
    return "\n".join(f"- {w}" for w in warnings)
