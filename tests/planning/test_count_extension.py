"""Pair gating and honest layer bounds against independent nonmonotone tables."""

import importlib.util
import itertools
from pathlib import Path

import pytest

from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.failure_cuts import FailureCutStore

SPEC = importlib.util.spec_from_file_location(
    "count_extension",
    Path(__file__).resolve().parents[2] / "scripts/extend_heritage_bounded_search.py",
)
extension = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(extension)


def solve(safe, *, blocked_count=None, guard_limit=None, inherited=None):
    areas = {letter: float(i + 1) for i, letter in enumerate("ABCDEF")}
    scenarios = ("gulou-h1", "gulou-h0.5", "bridge-h1", "bridge-h0.5")
    initial = tuple("ABCDE")
    cache = {(initial, s): ProtectionCheck("passed", {}) for s in scenarios}
    cache.update(inherited or {})
    pairs = list(itertools.combinations(areas, 2))
    stage, calls = [], []
    bounds = {3: 0, 4: 0}

    def evaluate(ids, scenario):
        if len(ids) == blocked_count:
            raise extension.StageLimit("test_spent_stage_budget")
        assert (ids, scenario) not in cache
        if len(ids) >= 3:
            assert all(extension.plan_status(p, scenarios, cache) == "failed" for p in pairs)
        calls.append((ids, scenario))
        return ProtectionCheck("passed" if ids in safe else "failed", {})

    def guard():
        count = stage[-1]
        bounds[count] += 1
        if guard_limit is not None and bounds[count] > guard_limit:
            raise extension.StageLimit("test_symbolic_frontier_budget")

    result = extension.continue_counts(
        areas,
        scenarios,
        cache,
        pairs,
        evaluate,
        FailureCutStore("frozen", areas),
        stage.append,
        guard,
        lambda _: None,
    )
    return result, calls, stage, scenarios, cache


def test_every_pair_is_classified_and_a_safe_pair_blocks_larger_layers():
    safe = {tuple("ABCDE"), ("B", "F"), ("C", "D")}
    result, calls, stages, _, _ = solve(safe)
    expected = min(safe, key=lambda ids: (len(ids), sum(ord(i) - 64 for i in ids)))
    assert result["best_ids"] == list(expected)
    assert len(result["pair_frontier"]) == 15 and result["pair_frontier_complete"]
    assert result["optimality_proven"] and result["minimum_count_proven"]
    assert result["objective_policy"] == "lexicographic_count_then_sum_nominal_footprint_area_m2"
    assert stages == [2] and all(len(ids) == 2 for ids, _ in calls)


def test_all_candidates_remain_available_and_nonmonotone_triple_is_found():
    # The remote F is essential, and the safe triple's four-house supersets fail.
    result, _, stages, _, _ = solve({tuple("ABCDE"), ("C", "E", "F")})
    assert result["best_ids"] == ["C", "E", "F"]
    assert result["minimum_count_lower_bound"] == 3 and result["minimum_count_proven"]
    assert stages == [2, 3] and not result["optimality_proven"]


def test_unfinished_triples_cannot_be_used_as_a_four_house_minimum_certificate():
    result, _, stages, _, _ = solve({tuple("ABCDE"), tuple("ABCD")}, blocked_count=3)
    assert result["best_ids"] == list("ABCD") and stages == [2, 3, 4]
    assert result["minimum_count_lower_bound"] == 3
    assert not result["minimum_count_proven"]
    assert not result["layers"][0]["all_combinations_failed_or_proved_failed"]


def test_full_triple_exclusion_supports_a_four_house_count_certificate():
    result, _, _, _, _ = solve({tuple("ABCDE"), tuple("ABCD")})
    assert result["minimum_count_lower_bound"] == 4 and result["minimum_count_proven"]
    assert result["layers"][0]["all_combinations_failed_or_proved_failed"]
    assert len(result["layers"][0]["visited_plans"]) == 20


def test_unresolved_pair_blocks_both_larger_layers():
    result, _, stages, _, _ = solve({tuple("ABCDE")}, blocked_count=2)
    assert not result["pair_frontier_complete"] and stages == [2]
    assert result["minimum_count_lower_bound"] == 2 and not result["minimum_count_proven"]
    assert result["pair_frontier"][0]["status"] == "unknown"


def test_symbolic_budget_retains_unknown_layers_without_launching_ca():
    result, calls, _, _, _ = solve({tuple("ABCDE")}, guard_limit=0)
    assert all(len(ids) == 2 for ids, _ in calls)
    assert result["minimum_count_lower_bound"] == 3 and not result["minimum_count_proven"]
    assert all(not layer["all_combinations_failed_or_proved_failed"] for layer in result["layers"])


def test_resume_reuses_verified_solution_without_spending_a_larger_layer_budget():
    scenarios = ("gulou-h1", "gulou-h0.5", "bridge-h1", "bridge-h0.5")
    known = tuple("CEF")
    inherited = {(known, s): ProtectionCheck("passed", {}) for s in scenarios}
    result, calls, stages, _, _ = solve({tuple("ABCDE"), known}, inherited=inherited)
    assert result["best_ids"] == list(known) and result["minimum_count_proven"]
    assert stages == [2] and all(len(ids) == 2 for ids, _ in calls)


def test_physical_unknown_is_not_converted_into_failed_or_successful_plan():
    with pytest.raises(ValueError, match="physical unknown"):
        extension.check_plan(("A", "B"), ("s",), {}, lambda *_: ProtectionCheck("unknown", {}))
