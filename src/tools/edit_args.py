"""Normalize edit tool arguments from legacy and pi-style shapes."""

from __future__ import annotations

import json
from typing import Any

from src.tools.edit_diff import EditError, EditReplacement


def _coerce_text(value: Any, field_name: str) -> str:
    """Coerce Text for this module."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        raise EditError(
            f"{field_name} must be a string, got list; "
            "use edits=[{{old_string: ..., new_string: ...}}] for multiple replacements"
        )
    if value is None:
        return ""
    return str(value)


def _parse_edit_item(raw: Any, index: int) -> EditReplacement:
    """Parse Edit Item for this module."""
    if not isinstance(raw, dict):
        raise EditError(f"edits[{index}] must be an object with old_string/new_string")

    old = raw.get("old_string")
    if old is None:
        old = raw.get("oldText")
    new = raw.get("new_string")
    if new is None:
        new = raw.get("newText")

    old_text = _coerce_text(old, f"edits[{index}].old_string")
    new_text = _coerce_text(new, f"edits[{index}].new_string")
    return EditReplacement(old_text=old_text, new_text=new_text)


def prepare_edit_arguments(**kwargs: Any) -> tuple[str, list[EditReplacement]]:
    """Fold legacy top-level patches and pi-style edits[] into a normalized list."""
    path = kwargs.get("path")
    if not path or not isinstance(path, str):
        raise EditError("path is required")

    edits: list[EditReplacement] = []
    raw_edits = kwargs.get("edits")

    if isinstance(raw_edits, str):
        try:
            parsed = json.loads(raw_edits)
        except json.JSONDecodeError as e:
            raise EditError(f"edits must be valid JSON when passed as a string: {e}") from e
        raw_edits = parsed

    if raw_edits is not None:
        if not isinstance(raw_edits, list):
            raise EditError("edits must be a list of replacement objects")
        for i, item in enumerate(raw_edits):
            edits.append(_parse_edit_item(item, i))

    old_string = kwargs.get("old_string")
    new_string = kwargs.get("new_string")
    if old_string is None and kwargs.get("oldText") is not None:
        old_string = kwargs.get("oldText")
    if new_string is None and kwargs.get("newText") is not None:
        new_string = kwargs.get("newText")

    if old_string is not None or new_string is not None:
        if old_string is None or new_string is None:
            raise EditError("Both old_string and new_string are required for legacy single-edit calls")
        edits.append(
            EditReplacement(
                old_text=_coerce_text(old_string, "old_string"),
                new_text=_coerce_text(new_string, "new_string"),
            )
        )

    if not edits:
        raise EditError("edits must contain at least one replacement")

    return path, edits
