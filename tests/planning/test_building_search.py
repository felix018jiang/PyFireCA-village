"""Optimality against independent exhaustive tables, including nonmonotone fires."""

import itertools
from math import fsum
from random import Random

import pytest

from pyfireca.planning.building_search import (
    ProtectionCheck,
    exact_demolition_search,
    ordered_demolition_sets,
)


def run(areas, safe, **kwargs):
    return exact_demolition_search(
        areas,
        ("gulou-h1", "bridge-h0.5"),
        lambda ids, _s: ProtectionCheck("passed" if safe(ids) else "failed", {}),
        problem_sha256="test-frozen-inputs",
        **kwargs,
    )


def test_lazy_area_order_matches_independent_enumeration():
    areas = {"A": 4.0, "B": 0.1, "C": 2.0, "D": 0.2, "E": 1.0}
    for count in range(6):
        got = list(ordered_demolition_sets(areas, count))
        expected = [
            (ids, fsum(areas[i] for i in ids))
            for ids in itertools.combinations(sorted(areas), count)
        ]
        assert set(got) == set(expected)
        assert [a for _, a in got] == sorted(a for _, a in expected)
        assert len(got) == len(set(got))
        assert set(ordered_demolition_sets(areas, count, area_limit=2.1)) == {
            x for x in expected if x[1] <= 2.1
        }


def test_joint_removal_and_cheaper_same_count_with_incumbent():
    areas = {"A": 9.0, "B": 3.0, "C": 2.0, "D": 1.0}

    def safe(ids):
        return {"B", "C"} <= set(ids) or {"A", "D"} <= set(ids)

    result = run(areas, safe, initial_ids=("A", "D"))
    assert result["status"] == "optimal"
    assert result["best_ids"] == ["B", "C"]
    assert result["best_objective"] == [2, 5.0]
    assert all(r["failed"] == r["total_combinations"] for r in result["layers"][:2])


def test_zero_removal_infeasibility_and_no_monotonicity_assumption():
    assert run({"A": 1}, lambda ids: not ids)["best_ids"] == []
    assert run({"A": 1}, lambda _ids: False)["status"] == "infeasible"
    # A three-house plan is safe, but a different single house is also safe;
    # its supersets are deliberately unsafe.
    result = run(
        {"A": 1, "B": 2, "C": 3, "D": 100},
        lambda ids: ids in {("D",), ("A", "B", "C")},
        initial_ids=("A", "B", "C"),
    )
    assert result["best_ids"] == ["D"] and result["optimality_proven"]


def test_random_nonmonotone_tables_match_brute_force():
    rng = Random(20261024)
    areas = {str(i): float(rng.randint(1, 30)) for i in range(7)}
    combinations = [ids for k in range(8) for ids in itertools.combinations(sorted(areas), k)]
    for _ in range(30):
        safe = {ids for ids in combinations if rng.random() < 0.08}
        expected = min(safe, key=lambda ids: (len(ids), fsum(areas[i] for i in ids)), default=None)
        result = run(areas, lambda ids, safe=safe: ids in safe)
        assert result["optimality_proven"] == (expected is not None)
        if expected is not None:
            assert result["best_objective"] == [len(expected), fsum(areas[i] for i in expected)]


def test_unknown_does_not_become_a_failure_or_an_optimality_certificate():
    def evaluate(ids, _s):
        return ProtectionCheck("unknown" if not ids else "passed", {"reason": "missing"})

    result = exact_demolition_search(
        {"A": 1}, ("s1", "s2"), evaluate, problem_sha256="p", initial_ids=("A",)
    )
    assert result["status"] == "incomplete" and result["best_ids"] == ["A"]
    assert result["proof_blockers"] == [[]]


def test_valid_failure_overrides_unknown_and_early_stops():
    calls = []

    def evaluate(ids, scenario):
        calls.append((ids, scenario))
        return ProtectionCheck("unknown" if scenario == "s1" else "failed", {})

    result = exact_demolition_search({}, ("s1", "s2", "s3"), evaluate, problem_sha256="p")
    assert result["status"] == "infeasible"
    assert len(calls) == 2


def test_count_certificate_can_hold_when_area_certificate_is_blocked():
    def evaluate(ids, _scenario):
        status = "failed" if not ids else "unknown" if ids == ("A",) else "passed"
        return ProtectionCheck(status, {})

    result = exact_demolition_search(
        {"A": 1, "B": 2},
        ("s",),
        evaluate,
        problem_sha256="count-versus-area",
        initial_ids=("B",),
    )
    assert result["minimum_count_proven"] and not result["optimality_proven"]
    assert result["best_ids"] == ["B"] and result["proof_blockers"] == [["A"]]


def test_budget_resume_cache_and_changed_problem():
    areas, cache = {"A": 1, "B": 2, "C": 3}, {}
    first = run(areas, lambda ids: ids == ("B", "C"), cache=cache, max_new_checks=2)
    assert first["status"] == "incomplete" and first["new_checks"] == 2
    resumed = run(areas, lambda ids: ids == ("B", "C"), cache=cache)
    assert resumed["optimality_proven"] and resumed["cache_hits"] >= 2
    unchanged = run(areas, lambda _ids: pytest.fail("cached search must not evaluate"), cache=cache)
    assert unchanged["new_checks"] == 0
    changed = exact_demolition_search(
        areas,
        ("gulou-h1", "bridge-h0.5"),
        lambda _ids, _s: ProtectionCheck("passed", {}),
        problem_sha256="changed-inputs",
        cache=cache,
    )
    assert changed["new_checks"] == 2 and changed["best_ids"] == []


def test_invalid_initial_and_missing_areas_raise():
    with pytest.raises(ValueError, match="initial feasible"):
        run({"A": 1}, lambda _ids: False, initial_ids=("A",))
    with pytest.raises(ValueError, match="finite and positive"):
        run({"A": float("nan")}, lambda _ids: True)
    with pytest.raises(ValueError, match="unique candidate"):
        run({"A": 1}, lambda _ids: True, initial_ids=("other",))


def test_area_subtree_pruning_counts_exactly_the_excluded_combinations():
    areas = {"A": 1, "B": 2, "C": 4, "D": 8, "E": 16}
    for count in range(6):
        stats = {"area_pruned_prefixes": 0, "area_pruned_combinations": 0}
        kept = list(ordered_demolition_sets(areas, count, area_limit=10, statistics=stats))
        excluded = [
            ids for ids in itertools.combinations(areas, count) if fsum(areas[i] for i in ids) > 10
        ]
        assert stats["area_pruned_combinations"] == len(excluded)
        assert len(kept) + len(excluded) == len(list(itertools.combinations(areas, count)))


def test_proven_identity_dominance_matches_unpruned_nonmonotone_table():
    areas = {"A": 4, "B": 3, "C": 1, "identity": 0.1}

    def safe(ids):
        return set(ids) - {"identity"} in ({"A", "B"}, {"C"})

    plain = run(areas, safe, initial_ids=("A", "B"))
    pruned = run(
        areas,
        safe,
        initial_ids=("A", "B"),
        dominated_candidates={"identity": {"problem_sha256": "test-frozen-inputs"}},
    )
    assert plain["best_objective"] == pruned["best_objective"] == [1, 1.0]
    assert pruned["optimality_proven"]
    assert pruned["new_checks"] < plain["new_checks"]
    assert sum(r["dominance_pruned_combinations"] for r in pruned["layers"]) > 0
    with pytest.raises(ValueError, match="dominance proofs"):
        run(areas, safe, dominated_candidates={"identity": {"problem_sha256": "other"}})


def test_failure_scheduling_reduces_checks_without_changing_the_certificate():
    areas = {"A": 1, "B": 2, "C": 3}

    options = {"problem_sha256": "p", "initial_ids": ("A", "B", "C")}

    # Empty plan learns the late failure; the next nearest plan also fails there.
    def evaluate(ids, scenario):
        return ProtectionCheck("failed" if len(ids) < 3 and scenario == "late" else "passed", {})

    plain = exact_demolition_search(areas, ("early", "late"), evaluate, **options)
    fast = exact_demolition_search(
        areas, ("early", "late"), evaluate, prioritize_failures=True, **options
    )
    assert fast["optimality_proven"] and fast["best_objective"] == plain["best_objective"]
    assert fast["new_checks"] < plain["new_checks"]
    resumed = exact_demolition_search(
        areas,
        ("early", "late"),
        lambda *_: pytest.fail("cache must reproduce scheduled search"),
        cache={
            ("p", tuple(row["demolished_ids"]), check["scenario_id"]): ProtectionCheck(
                check["status"], check["evidence"]
            )
            for row in fast["trace"]
            for check in row["checks"]
        },
        prioritize_failures=True,
        **options,
    )
    assert resumed["new_checks"] == 0 and resumed["trace"] == fast["trace"]


def test_soft_wall_budget_does_not_create_a_certificate():
    result = run({"A": 1}, lambda _ids: True, max_wall_time_s=0)
    assert result["new_checks"] == 0 and result["status"] == "incomplete"
    assert result["budget_stop_reason"] == "wall_time_limit"
