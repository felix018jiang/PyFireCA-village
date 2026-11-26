"""Stage ordering, objective proof and continuation budgets on known truth tables."""

import importlib.util
import itertools
from pathlib import Path

import pytest

PATH = Path(__file__).resolve().parents[2] / "scripts/complete_heritage_exact_eight.py"
SPEC = importlib.util.spec_from_file_location("completion", PATH)
completion = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(completion)


@pytest.mark.parametrize("safe_pair", [None, ("A", "D")])
def test_completion_obeys_pair_gate_and_matches_independent_optimum(safe_pair):
    areas = {"A": 3.0, "B": 2.0, "C": 4.0, "D": 1.0}
    scenarios = ["early", "late"]
    initial = ("A", "B", "C")
    cache = {(initial, s): {"status": "passed", "evidence": {}} for s in scenarios}
    for k in (0, 1):
        for p in itertools.combinations(sorted(areas), k):
            cache[p, "late"] = {"status": "failed", "evidence": {}}
    original = dict(cache)
    pending = {
        2: list(itertools.combinations(sorted(areas), 2)),
        3: [("A", "B", "D"), ("A", "C", "D"), ("B", "C", "D")],
    }
    calls = []

    def evaluate(ids, scenario):
        assert (ids, scenario) not in original
        if len(ids) == 3:
            assert all(completion.plan_status(p, scenarios, cache) == "failed" for p in pending[2])
        calls.append((ids, scenario))
        safe = ids in (initial, ("A", "B", "D"), safe_pair)
        return {"status": "passed" if safe or scenario == "early" else "failed", "evidence": {}}

    budget = {"max_new_checks": 18, "stage_limits": {"2": 12, "3": 6}, "max_wall_time_s": 10}
    keys = set()
    result = completion.complete_stages(
        areas, scenarios, cache, pending, evaluate, new_keys=keys, budget=budget
    )
    expected = list(safe_pair or ("A", "B", "D"))
    assert result["optimality_proven"] and result["best_ids"] == expected
    assert all(completion.plan_status(p, scenarios, cache) != "unknown" for p in pending[2])
    if safe_pair:
        assert not any(len(p) == 3 for p, _ in calls)
    again = completion.complete_stages(
        areas,
        scenarios,
        cache,
        pending,
        lambda *_: pytest.fail("completed proof must resume without new calls"),
        new_keys=keys,
        budget=budget,
    )
    assert again["best_ids"] == result["best_ids"]
    assert len(keys) == len(calls)


def test_unknown_pair_blocks_triples_and_budget_does_not_reset_on_resume():
    areas, scenarios = {"A": 1, "B": 2, "C": 3}, ["s1", "s2"]
    initial = ("A", "B", "C")
    cache = {(initial, s): {"status": "passed", "evidence": {}} for s in scenarios}
    for k in (0, 1):
        for p in itertools.combinations(areas, k):
            cache[p, "s1"] = {"status": "failed", "evidence": {}}
    pending = {2: list(itertools.combinations(areas, 2)), 3: []}
    budget = {"max_new_checks": 1, "stage_limits": {"2": 1, "3": 0}, "max_wall_time_s": 10}
    keys = set()

    def evaluate(*_):
        return {"status": "passed", "evidence": {}}

    result = completion.complete_stages(
        areas, scenarios, cache, pending, evaluate, new_keys=keys, budget=budget
    )
    assert result["status"] == "incomplete" and result["budget_stop_reason"] == "check_limit"
    assert result["new_checks_total"] == 1 and not result["optimality_proven"]
    resumed = completion.complete_stages(
        areas,
        scenarios,
        cache,
        pending,
        lambda *_: pytest.fail("spent budget cannot be reset"),
        new_keys=keys,
        budget=budget,
    )
    assert resumed["new_checks_total"] == 1 and resumed["status"] == "incomplete"
