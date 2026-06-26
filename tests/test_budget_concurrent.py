"""Concurrent budget tracker tests."""

from __future__ import annotations

import concurrent.futures

import pytest

from src.core.skill_scripts.budget import check_and_decrement, init_budget


def test_budget_concurrent_decrements(tmp_path):
    init_budget("research", "parallel-thread", hard_limit=10, budget_dir=tmp_path)

    def _decrement() -> int:
        state = check_and_decrement("research", "parallel-thread", 10, budget_dir=tmp_path)
        return state["calls_made"]

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda _: _decrement(), range(6)))

    assert sorted(results) == [1, 2, 3, 4, 5, 6]
