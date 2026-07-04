"""Prediction — the normalized output of a pipeline run for one case.

Every target (routing / research / plan / implement / e2e) fills the subset of
fields it produces; evaluators read from here. Keeping a single flat shape makes
the offline (fixture) and live paths interchangeable.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field


class Prediction(BaseModel):
    """Normalized per-case pipeline output — route, files, plan fields, patch text.
    Built by `harness/targets.py`; input to all evaluators and `gates.py` checks."""
    case_id: str

    # routing
    route: Optional[str] = None

    # research
    files: list[str] = Field(default_factory=list)
    research_summary: str = ""

    # plan
    plan_files: list[str] = Field(default_factory=list)
    plan_fix_type: str = ""
    plan_line_ranges: list[dict[str, Any]] = Field(default_factory=list)  # {file,start,end}

    # implement
    patch_text: str = ""
    changed_files: list[str] = Field(default_factory=list)
    compilation_status: str = ""

    # diagnostics
    error: Optional[str] = None
    stage_failed: Optional[str] = None
    token_usage: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Prediction":
        """From Dict for Prediction."""
        return cls.model_validate(data)


def load_predictions(target: str | Path) -> dict[str, Prediction]:
    """Load predictions from a dir of <case_id>.json files or a single json map."""
    p = Path(target)
    out: dict[str, Prediction] = {}
    if p.is_dir():
        for f in sorted(p.glob("*.json")):
            pred = Prediction.from_dict(json.loads(f.read_text(encoding="utf-8")))
            out[pred.case_id] = pred
        return out
    raw = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(raw, list):
        for item in raw:
            pred = Prediction.from_dict(item)
            out[pred.case_id] = pred
    else:  # {case_id: {...}}
        for cid, item in raw.items():
            item = {"case_id": cid, **item}
            out[cid] = Prediction.from_dict(item)
    return out
