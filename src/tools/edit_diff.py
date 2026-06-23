"""Edit matching engine — normalized exact + fuzzy replacement (pi edit-diff port)."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

LineEnding = Literal["\r\n", "\n"]

BOM = "\ufeff"

_SMART_QUOTES = {
    "\u201c": '"',
    "\u201d": '"',
    "\u2018": "'",
    "\u2019": "'",
}
_DASHES = {
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
}
_SPECIAL_SPACES = {
    "\u00a0": " ",
    "\u2002": " ",
    "\u2003": " ",
    "\u2009": " ",
}


class EditError(Exception):
    """Raised when an edit cannot be applied safely."""


@dataclass(frozen=True)
class EditReplacement:
    old_text: str
    new_text: str


@dataclass(frozen=True)
class FuzzyMatchResult:
    found: bool
    index: int
    match_length: int
    used_fuzzy_match: bool
    content_for_replacement: str


def detect_line_ending(content: str) -> LineEnding:
    """Return dominant line ending; default LF when ambiguous."""
    crlf = content.count("\r\n")
    lf_only = content.count("\n") - crlf
    if crlf > lf_only:
        return "\r\n"
    return "\n"


def normalize_to_lf(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def restore_line_endings(text: str, ending: LineEnding) -> str:
    if ending == "\r\n":
        return text.replace("\n", "\r\n")
    return text


def strip_bom(content: str) -> tuple[str, str]:
    if content.startswith(BOM):
        return BOM, content[1:]
    return "", content


def normalize_for_fuzzy_match(text: str) -> str:
    normalized = unicodedata.normalize("NFKC", text)
    for src, dst in _SMART_QUOTES.items():
        normalized = normalized.replace(src, dst)
    for src, dst in _DASHES.items():
        normalized = normalized.replace(src, dst)
    for src, dst in _SPECIAL_SPACES.items():
        normalized = normalized.replace(src, dst)
    lines = [line.rstrip(" \t") for line in normalized.split("\n")]
    return "\n".join(lines)


def fuzzy_find_text(content: str, old_text: str) -> FuzzyMatchResult:
    exact_index = content.find(old_text)
    if exact_index != -1:
        return FuzzyMatchResult(
            found=True,
            index=exact_index,
            match_length=len(old_text),
            used_fuzzy_match=False,
            content_for_replacement=content,
        )

    fuzzy_content = normalize_for_fuzzy_match(content)
    fuzzy_old = normalize_for_fuzzy_match(old_text)
    fuzzy_index = fuzzy_content.find(fuzzy_old)
    if fuzzy_index == -1:
        return FuzzyMatchResult(
            found=False,
            index=-1,
            match_length=0,
            used_fuzzy_match=False,
            content_for_replacement=content,
        )

    return FuzzyMatchResult(
        found=True,
        index=fuzzy_index,
        match_length=len(fuzzy_old),
        used_fuzzy_match=True,
        content_for_replacement=fuzzy_content,
    )


def _count_occurrences(content: str, old_text: str, *, fuzzy: bool) -> int:
    if not old_text:
        return 0
    if fuzzy:
        haystack = normalize_for_fuzzy_match(content)
        needle = normalize_for_fuzzy_match(old_text)
    else:
        haystack = content
        needle = old_text
    if not needle:
        return 0
    count = 0
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        count += 1
        start = idx + len(needle)
    return count


def _nearby_lines(content: str, old_text: str, limit: int = 3) -> list[str]:
    first_line = old_text.splitlines()[0] if old_text else ""
    tokens = [t for t in re.split(r"\W+", first_line) if len(t) > 2][:5]
    if not tokens:
        return []
    matches: list[str] = []
    for line in content.splitlines():
        if any(token in line for token in tokens):
            matches.append(line)
            if len(matches) >= limit:
                break
    return matches


def _format_not_found(path: str, old_text: str, content: str) -> str:
    first_line = old_text.splitlines()[0] if old_text else "(empty)"
    nearby = _nearby_lines(content, old_text)
    lines = [
        f"Could not find the exact text in {path}.",
        "The old text must match exactly including all whitespace and newlines.",
        f"Searched for (first line): {first_line!r}",
    ]
    if nearby:
        lines.append("Nearby lines in file:")
        for line in nearby:
            lines.append(f"  {line}")
    lines.append("Copy old_string verbatim from read output, including indentation and quotes.")
    return "\n".join(lines)


def _format_multiple_matches(path: str, old_text: str, count: int) -> str:
    first_line = old_text.splitlines()[0] if old_text else "(empty)"
    return (
        f"Found {count} occurrences of the old text in {path}; it must be unique. "
        f"Please provide more context.\n"
        f"First line searched: {first_line!r}"
    )


def _regions_overlap(a_start: int, a_end: int, b_start: int, b_end: int) -> bool:
    return a_start < b_end and b_start < a_end


def apply_edits_to_normalized_content(
    normalized_content: str,
    edits: list[EditReplacement],
    path: str,
) -> tuple[str, str]:
    """Match all edits against original normalized content; return (base, new)."""
    if not edits:
        raise EditError("edits must contain at least one replacement")

    work_content = normalized_content
    use_fuzzy_space = False
    resolved: list[tuple[int, int, EditReplacement, FuzzyMatchResult]] = []

    for edit in edits:
        old_norm = normalize_to_lf(edit.old_text)
        new_norm = normalize_to_lf(edit.new_text)

        if not old_norm:
            raise EditError(f"old_text must not be empty in {path}")
        if old_norm == new_norm:
            raise EditError(
                f"No changes made in {path}: replacement produced identical content "
                f"for old_text starting with {old_norm.splitlines()[0]!r}"
            )

        match = fuzzy_find_text(work_content, old_norm)
        if not match.found:
            raise EditError(_format_not_found(path, old_norm, work_content))

        fuzzy = match.used_fuzzy_match
        count = _count_occurrences(work_content, old_norm, fuzzy=fuzzy)
        if count > 1:
            raise EditError(_format_multiple_matches(path, old_norm, count))

        if fuzzy:
            use_fuzzy_space = True
        resolved.append((match.index, match.match_length, edit, match))

    if use_fuzzy_space:
        work_content = normalize_for_fuzzy_match(work_content)
        resolved = []
        for edit in edits:
            old_norm = normalize_for_fuzzy_match(normalize_to_lf(edit.old_text))
            new_norm = normalize_to_lf(edit.new_text)
            if not old_norm:
                raise EditError(f"old_text must not be empty in {path}")
            idx = work_content.find(old_norm)
            if idx == -1:
                raise EditError(_format_not_found(path, old_norm, normalized_content))
            count = _count_occurrences(work_content, old_norm, fuzzy=True)
            if count > 1:
                raise EditError(_format_multiple_matches(path, old_norm, count))
            match = FuzzyMatchResult(
                found=True,
                index=idx,
                match_length=len(old_norm),
                used_fuzzy_match=True,
                content_for_replacement=work_content,
            )
            resolved.append((idx, len(old_norm), EditReplacement(old_text=edit.old_text, new_text=new_norm), match))

    regions: list[tuple[int, int, EditReplacement]] = []
    for index, length, edit, _match in resolved:
        regions.append((index, index + length, edit))

    for i, (a_start, a_end, _) in enumerate(regions):
        for j in range(i + 1, len(regions)):
            b_start, b_end, _ = regions[j]
            if _regions_overlap(a_start, a_end, b_start, b_end):
                raise EditError(
                    f"edits[{i}] and edits[{j}] overlap in {path}; "
                    "merge overlapping changes into one edit"
                )

    new_content = work_content
    for index, length, edit, _match in sorted(resolved, key=lambda r: r[0], reverse=True):
        replacement = normalize_to_lf(edit.new_text)
        if use_fuzzy_space:
            replacement = normalize_for_fuzzy_match(replacement)
        new_content = new_content[:index] + replacement + new_content[index + length :]

    return normalized_content, new_content
