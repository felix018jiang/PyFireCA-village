"""Area policy and finite-study evidence, independent of village identifiers."""

from itertools import combinations

import pytest

from pyfireca.planning.area_search import search_area_candidates
from pyfireca.planning.building_search import ProtectionCheck


def passed(count=0, area=0):
    return ProtectionCheck(
        "passed",
        {"loss_metrics": {"ignited_building_count_upper": count, "ignited_roof_area_m2": area}},
    )


def test_four_small_interventions_beat_three_large_without_cardinality_pruning():
    areas = dict.fromkeys(("a", "b", "c"), 10) | dict.fromkeys(("w", "x", "y", "z"), 1)
    feasible = {("a", "b", "c"), ("w", "x", "y", "z")}
    result = search_area_candidates(
        areas,
        ("s",),
        lambda ids, _: passed() if ids in feasible else ProtectionCheck("failed", {}),
        lambda ids: sum(areas[i] for i in ids),
        problem_sha256="frozen",
    )
    assert result["best_ids"] == ["w", "x", "y", "z"]
    assert result["best_area_m2"] == 4
    assert result["cardinality_pruned_combinations"] == 0
    assert result["global_minimum_area_proven"]
    assert result["full_candidate_search_complete"]
    assert result["new_checks"] == 128


def test_shared_fuel_cost_is_a_complete_set_function_not_sum_of_singletons():
    # Removing either shared owner alone clears one unit; removing both also
    # clears their ten-unit shared roof. Additive costs would choose a+b (2).
    area = {(): 0, ("a",): 1, ("b",): 1, ("c",): 4, ("a", "b"): 12}
    plans = list(area)
    result = search_area_candidates(
        ("a", "b", "c"),
        ("s",),
        lambda ids, _: passed() if ids in {("a", "b"), ("c",)} else ProtectionCheck("failed", {}),
        area.__getitem__,
        problem_sha256="shared-geometry",
        plans=plans,
        plan_sources={("a", "b"): "shared-owner alternative", ("c",): "independent building"},
    )
    assert result["best_ids"] == ["c"]
    assert [row["cleared_area_m2"] for row in result["trace"]] == [0, 1, 1, 4, 12]
    assert result["trace"][-1]["plan_source"] == "shared-owner alternative"
    assert result["minimum_area_proven_within_scope"]
    assert not result["global_minimum_area_proven"]
    assert result["subsets_outside_scope"] == 3


def test_nonmonotone_area_cannot_prune_a_cheaper_superset():
    areas = {(): 0, ("a",): 9, ("b",): 10, ("a", "b"): 1}
    result = search_area_candidates(
        ("a", "b"),
        ("s",),
        lambda ids, _: passed() if ids else ProtectionCheck("failed", {}),
        areas.__getitem__,
        problem_sha256="general-set-function",
    )
    assert result["best_ids"] == ["a", "b"]
    assert result["global_minimum_area_proven"]


def test_unknown_cheaper_plan_blocks_area_proof_and_never_enters_frontier():
    def evaluate(ids, scenario):
        if not ids:
            return ProtectionCheck("failed", {})
        if ids == ("a",) and scenario == "second":
            return ProtectionCheck("unknown", {"reason": "interrupted"})
        return passed(1, 2)

    result = search_area_candidates(
        ("a", "b"),
        ("first", "second"),
        evaluate,
        lambda ids: sum({"a": 1, "b": 2}[i] for i in ids),
        problem_sha256="unknown-propagation",
        loss_metrics=("ignited_building_count_upper", "ignited_roof_area_m2"),
    )
    assert result["best_ids"] == ["b"]
    assert result["minimum_area_proof_blockers"] == [["a"]]
    assert result["unresolved_plans"] == [["a"]]
    assert not result["minimum_area_proven_within_scope"]
    assert result["global_minimum_area_lower_bound_m2"] == 1
    assert [row["demolished_ids"] for row in result["pareto_frontier"]] == [["b"]]
    assert not result["global_pareto_frontier_complete"]


def test_failure_after_unknown_is_a_valid_failure_but_not_a_complete_loss_estimate():
    result = search_area_candidates(
        (),
        ("first", "second", "third"),
        lambda _, s: ProtectionCheck("failed" if s == "second" else "unknown", {}),
        lambda _: 0,
        problem_sha256="failure-witness",
    )
    assert result["status"] == "infeasible_in_scope"
    assert result["scope_complete"]
    assert result["trace"][0]["status"] == "failed"
    assert result["trace"][0]["objective_values"] is None
    assert result["trace"][0]["not_checked_scenario_ids"] == ["third"]
    assert result["new_checks"] == 2


def test_conflicting_area_count_and_fire_area_objectives_survive_in_archive():
    outcomes = {
        ("a",): {"s1": (1, 50), "s2": (4, 20)},
        ("b",): {"s1": (3, 15), "s2": (2, 30)},
        ("c",): {"s1": (2, 10), "s2": (1, 5)},
        ("d",): {"s1": (5, 60), "s2": (4, 55)},
    }
    result = search_area_candidates(
        ("a", "b", "c", "d"),
        ("s1", "s2"),
        lambda ids, s: passed(*outcomes[ids][s]),
        lambda ids: {"a": 1, "b": 2, "c": 3, "d": 4}[ids[0]],
        problem_sha256="tradeoffs",
        plans=outcomes,
        loss_metrics=("ignited_building_count_upper", "ignited_roof_area_m2"),
    )
    assert [row["objective_values"] for row in result["pareto_frontier"]] == [
        [1, 4, 50],
        [2, 3, 30],
        [3, 2, 10],
    ]
    assert result["best_ids"] == ["a"]
    assert result["pareto_frontier_complete_within_scope"]
    assert not result["global_pareto_frontier_complete"]
    assert result["full_candidate_space_unresolved"]


def test_budget_resume_cache_and_problem_identity_remain_explicit():
    cache = {}

    def evaluate(ids, _):
        return passed() if ids else ProtectionCheck("failed", {})

    arguments = {
        "candidate_ids": ("a",),
        "scenario_ids": ("s1", "s2"),
        "evaluate": evaluate,
        "cleared_area": len,
        "problem_sha256": "p",
        "cache": cache,
    }
    interrupted = search_area_candidates(**arguments, max_new_checks=2)
    assert interrupted["best_ids"] is None
    assert interrupted["trace"][1]["status"] == "unknown"
    assert interrupted["trace"][1]["not_checked_scenario_ids"] == ["s2"]
    no_reset = search_area_candidates(**arguments, max_new_checks=2, previous_new_checks=2)
    assert no_reset["new_checks"] == 0
    assert no_reset["budget_exhausted"]
    completed = search_area_candidates(**arguments, max_new_checks=3, previous_new_checks=2)
    assert completed["new_checks"] == 1
    assert completed["cumulative_new_checks"] == 3
    assert completed["cache_hits"] == 2
    assert completed["global_minimum_area_proven"]
    assert completed["global_pareto_frontier_complete"]
    changed = search_area_candidates(**(arguments | {"problem_sha256": "other"}))
    assert changed["new_checks"] == 3 and changed["cache_hits"] == 0


def test_equal_area_unknown_does_not_block_area_minimum_but_blocks_complete_frontier():
    result = search_area_candidates(
        ("a", "b"),
        ("s",),
        lambda ids, _: passed() if ids == ("a",) else ProtectionCheck("unknown", {}),
        lambda _: 1,
        problem_sha256="area-ties",
        plans=(("a",), ("b",)),
    )
    assert result["minimum_area_proven_within_scope"]
    assert not result["selected_tie_break_objective_proven_within_scope"]
    assert not result["pareto_frontier_complete_within_scope"]
    assert not result["global_minimum_area_proven"]


def test_large_candidate_inventory_requires_explicit_scope_and_is_reported():
    candidates = tuple(f"b{i}" for i in range(947))
    with pytest.raises(ValueError, match="supply explicit plans"):
        search_area_candidates(candidates, ("s",), None, len, problem_sha256="village")
    result = search_area_candidates(
        candidates,
        ("s",),
        lambda *_: passed(),
        len,
        problem_sha256="village",
        plans=((), (candidates[0],)),
    )
    assert result["full_candidate_count"] == 947
    assert result["full_subset_count"] == 2**947
    assert result["declared_plan_count"] == 2
    assert result["scope_complete"] and result["full_candidate_space_unresolved"]


def test_small_full_scope_matches_independent_brute_force_frontier():
    candidates = ("a", "b", "c", "d")
    all_plans = [ids for count in range(5) for ids in combinations(candidates, count)]
    areas = {ids: len(ids) + (2 if {"a", "b"} <= set(ids) else 0) for ids in all_plans}
    objectives = {
        ids: [areas[ids], 10 - 2 * len(ids), 12 - 3 * len(ids)] for ids in all_plans if ids
    }
    expected = {
        ids
        for ids, values in objectives.items()
        if not any(
            all(a <= b for a, b in zip(other, values, strict=True))
            and any(a < b for a, b in zip(other, values, strict=True))
            for other in objectives.values()
        )
    }
    result = search_area_candidates(
        candidates,
        ("s",),
        lambda ids, _: passed(*objectives[ids][1:]) if ids else ProtectionCheck("failed", {}),
        areas.__getitem__,
        problem_sha256="small-reference",
        loss_metrics=("ignited_building_count_upper", "ignited_roof_area_m2"),
    )
    assert {tuple(row["demolished_ids"]) for row in result["pareto_frontier"]} == expected
    assert result["global_pareto_frontier_complete"]


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1, True])
def test_invalid_area_and_passed_loss_cannot_enter_proof(bad):
    with pytest.raises(ValueError, match="cleared area"):
        search_area_candidates((), ("s",), None, lambda _: bad, problem_sha256="p")
    with pytest.raises(ValueError, match="loss metric"):
        search_area_candidates(
            (),
            ("s",),
            lambda *_: passed(area=bad),
            lambda _: 0,
            problem_sha256="p",
            loss_metrics=("ignited_roof_area_m2",),
        )


def test_missing_loss_and_invalid_cache_do_not_silently_create_feasible_archive():
    with pytest.raises(ValueError, match="missing declared loss"):
        search_area_candidates(
            (),
            ("s",),
            lambda *_: ProtectionCheck("passed", {}),
            lambda _: 0,
            problem_sha256="p",
            loss_metrics=("ignited_roof_area_m2",),
        )
    with pytest.raises(TypeError, match="cached oracle"):
        search_area_candidates(
            (), ("s",), None, lambda _: 0, problem_sha256="p", cache={("p", (), "s"): True}
        )
