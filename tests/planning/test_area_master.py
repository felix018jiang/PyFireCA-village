"""Small exhaustive references for the complete-action area master."""

import itertools
import random

import pytest

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.area_master import solve_area_master
from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.heritage import content_hash

pytest.importorskip("scipy.optimize")


def certificate(ids, retained, *, problem="p", scenario="s", source=None):
    if source is None and retained:
        source = sorted(retained)[0]
    value = {
        "schema_version": "heritage-cold-removal-cut-1",
        "problem_sha256": problem,
        "scenario_id": scenario,
        "required_demolished_ids": sorted(ids),
        "required_retained_ids": sorted(retained),
    }
    if source is not None:
        value["source_id"] = source
    value["certificate_sha256"] = content_hash(value)
    return ProtectionCheck("failed", {"failure_certificate": value})


def area(owners, values):
    return build_cleared_area_function(owners, values, area_basis="physical_geometry_m2")


def powerset(ids):
    return [group for count in range(len(ids) + 1) for group in itertools.combinations(ids, count)]


def test_shared_and_fixed_owner_area_matches_full_brute_force_after_failures():
    candidates = ("a", "b", "c", "d")
    physical = area(
        (("a",), ("b",), ("c",), ("d",), ("a", "b"), ("b", "fixed")), (1, 1, 4, 8, 10, 100)
    )
    witnesses = [
        ((), {"a", "c"}),
        (("a",), {"b", "d"}),
    ]
    cache = {("p", ids, "s"): certificate(ids, retained) for ids, retained in witnesses}
    result = solve_area_master(candidates, physical, ("s",), problem_sha256="p", cache=cache)
    eligible = [
        ids
        for ids in powerset(candidates)
        if not any(set(a) <= set(ids) and not set(h) & set(ids) for a, h in witnesses)
    ]
    minimum = min(physical(ids) for ids in eligible)
    assert result["next_area_m2"] == minimum == 4
    assert physical(result["next_ids"]) == minimum
    assert result["mip_dual_bound_m2"] == pytest.approx(minimum)
    assert result["mip_gap"] == 0
    assert result["unclearable_fixed_owner_group_count"] == 1
    assert result["shared_patch_variable_count"] == 1
    assert result["failure_cut_count"] == 2
    assert result["next_physical_protection_status"] == "unknown"
    assert not result["physical_optimality_proven"]


@pytest.mark.parametrize("seed", range(10))
def test_random_shared_patch_and_cold_cut_systems_match_complete_enumeration(seed):
    rng = random.Random(seed)
    candidates = ("a", "b", "c", "d", "e")
    owners = [(i,) for i in candidates]
    owners.extend(tuple(rng.sample(candidates, 2)) for _ in range(4))
    owners.append(("fixed",))
    physical = area(owners, [rng.randrange(9) for _ in owners])
    witnesses = []
    cache = {}
    scenarios = ("s0", "s1", "s2")
    for scenario in scenarios:
        removed = tuple(sorted(rng.sample(candidates, rng.randrange(3))))
        remaining = sorted(set(candidates) - set(removed))
        retained = tuple(sorted(rng.sample(remaining, rng.randrange(len(remaining) + 1))))
        witnesses.append((set(removed), set(retained)))
        cache[("p", removed, scenario)] = certificate(
            removed, (*retained, "fixed"), scenario=scenario, source="fixed"
        )
    possibilities = [
        ids
        for ids in powerset(candidates)
        if not any(a <= set(ids) and not h & set(ids) for a, h in witnesses)
    ]
    result = solve_area_master(
        candidates,
        physical,
        scenarios,
        problem_sha256="p",
        cache=cache,
        fixed_retained_ids=("fixed",),
    )
    if possibilities:
        expected = min(physical(ids) for ids in possibilities)
        assert result["next_area_m2"] == pytest.approx(expected)
        assert result["mip_dual_bound_m2"] == pytest.approx(expected)
        assert tuple(result["next_ids"]) in possibilities
    else:
        assert result["remaining_scope_empty"]
        assert result["next_ids"] is None


def test_master_never_uses_count_priority_to_exclude_four_small_actions():
    candidates = ("A", "B", "C", "w", "x", "y", "z")
    physical = area(((i,) for i in candidates), (10, 10, 10, 1, 1, 1, 1))
    permitted = {("A", "B", "C"), ("w", "x", "y", "z")}
    cache = {
        ("p", ids, "s"): ProtectionCheck("failed", {})
        for ids in powerset(candidates)
        if ids not in permitted
    }
    result = solve_area_master(candidates, physical, ("s",), problem_sha256="p", cache=cache)
    assert result["next_ids"] == ["w", "x", "y", "z"]
    assert result["next_area_m2"] == 4
    assert result["mip_dual_bound_m2"] == 4


def test_passed_exact_plan_is_excluded_only_after_all_scenarios_and_bound_is_remaining_scope():
    physical = area((("a",), ("b",)), (1, 2))
    cache = {
        ("p", (), "first"): ProtectionCheck("failed", {}),
        ("p", ("a",), "first"): ProtectionCheck("passed", {}),
        ("p", ("a",), "second"): ProtectionCheck("unknown", {}),
    }
    arguments = {"problem_sha256": "p", "cache": cache}
    unknown = solve_area_master(("a", "b"), physical, ("first", "second"), **arguments)
    assert unknown["next_ids"] == ["a"]
    assert unknown["exact_plan_exclusion_count"] == 1
    cache[("p", ("a",), "second")] = ProtectionCheck("passed", {})
    assessed = solve_area_master(("a", "b"), physical, ("first", "second"), **arguments)
    assert assessed["next_ids"] == ["b"]
    assert assessed["mip_dual_bound_m2"] == 2
    assert assessed["known_passed_best_ids"] == ["a"]
    assert assessed["known_passed_best_area_m2"] == 1
    assert assessed["exact_plan_exclusion_count"] == 2
    assert not assessed["physical_optimality_proven"]


def test_unknown_cache_and_other_fingerprint_do_not_exclude_any_plan():
    physical = area((("a",),), (3,))
    result = solve_area_master(
        ("a",),
        physical,
        ("s",),
        problem_sha256="p",
        cache={
            ("p", (), "s"): ProtectionCheck("unknown", {}),
            ("different", (), "s"): ProtectionCheck("failed", {}),
        },
    )
    assert result["next_ids"] == []
    assert result["next_area_m2"] == 0
    assert result["exact_plan_exclusion_count"] == 0


def test_valid_cold_family_can_exhaust_scope_without_a_physical_feasible_claim():
    physical = area((("a",), ("fixed",)), (3, 1))
    cache = {("p", (), "s"): certificate((), (), source="fixed")}
    result = solve_area_master(
        ("a",), physical, ("s",), problem_sha256="p", cache=cache, fixed_retained_ids=("fixed",)
    )
    assert result["status"] == "infeasible"
    assert result["remaining_scope_empty"]
    assert result["next_ids"] is None
    assert result["remaining_master_lower_bound_m2"] is None
    assert not result["physical_optimality_proven"]


def test_source_outside_action_universe_is_fixed_retained_and_never_cleared():
    physical = area((("a",), ("a", "source"), ("source",)), (2, 100, 10))
    cache = {("p", (), "s"): certificate((), ("a", "source"), source="source")}
    result = solve_area_master(
        ("a",), physical, ("s",), problem_sha256="p", cache=cache, fixed_retained_ids=("source",)
    )
    assert result["next_ids"] == ["a"]
    assert result["next_area_m2"] == 2
    assert result["unclearable_fixed_owner_group_count"] == 2


@pytest.mark.parametrize("mutation", ["hash", "problem", "scenario", "plan", "passed", "source"])
def test_certificate_binding_is_required_before_family_exclusion(mutation):
    physical = area((("a",), ("b",)), (1, 2))
    check = certificate((), ("a",), source="a")
    value = dict(check.evidence["failure_certificate"])
    status = "failed"
    if mutation == "hash":
        value["certificate_sha256"] = "bad"
    elif mutation == "problem":
        value["problem_sha256"] = "other"
    elif mutation == "scenario":
        value["scenario_id"] = "other"
    elif mutation == "plan":
        value["required_demolished_ids"] = ["b"]
    elif mutation == "passed":
        status = "passed"
    else:
        value["required_retained_ids"] = ["b"]
    if mutation != "hash":
        value["certificate_sha256"] = content_hash(
            {k: v for k, v in value.items() if k != "certificate_sha256"}
        )
    with pytest.raises(ValueError, match="certificate"):
        solve_area_master(
            ("a", "b"),
            physical,
            ("s",),
            problem_sha256="p",
            cache={("p", (), "s"): ProtectionCheck(status, {"failure_certificate": value})},
        )


def test_cold_certificate_cannot_remove_its_required_retained_owner():
    physical = area((("a",),), (1,))
    check = certificate(("a",), ("a",))
    with pytest.raises(ValueError, match="invalid action"):
        solve_area_master(
            ("a",), physical, ("s",), problem_sha256="p", cache={("p", ("a",), "s"): check}
        )


def test_empty_action_universe_can_propose_then_exclude_empty_plan():
    physical = area((("fixed",),), (10,))
    result = solve_area_master((), physical, ("s",), problem_sha256="p")
    assert result["next_ids"] == [] and result["next_area_m2"] == 0
    exhausted = solve_area_master(
        (),
        physical,
        ("s",),
        problem_sha256="p",
        cache={("p", (), "s"): ProtectionCheck("passed", {})},
    )
    assert exhausted["remaining_scope_empty"]
    assert exhausted["known_passed_best_ids"] == []
    assert not exhausted["physical_optimality_proven"]


def test_cached_plan_identity_and_evidence_problem_must_be_valid():
    physical = area((("a",), ("b",)), (1, 2))
    for key, check in [
        (("p", ("b", "a"), "s"), ProtectionCheck("failed", {})),
        (("p", ("outside",), "s"), ProtectionCheck("failed", {})),
        (("p", (), "s"), ProtectionCheck("failed", {"problem_sha256": "other"})),
    ]:
        with pytest.raises(ValueError):
            solve_area_master(("a", "b"), physical, ("s",), problem_sha256="p", cache={key: check})


def test_master_requires_explicit_patch_and_model_binding():
    with pytest.raises(TypeError, match="physical-patch"):
        solve_area_master(("a",), lambda _: 1, ("s",), problem_sha256="p")
    with pytest.raises(ValueError, match="fingerprint"):
        solve_area_master(("a",), area((("a",),), (1,)), ("s",), problem_sha256="")


def test_outside_cut_owners_require_explicit_fixed_retained_declaration():
    physical = area((("a",), ("source",)), (2, 10))
    cache = {("p", (), "s"): certificate((), ("a", "source"), source="source")}
    with pytest.raises(ValueError, match="invalid action/owner"):
        solve_area_master(("a",), physical, ("s",), problem_sha256="p", cache=cache)
    with pytest.raises(ValueError, match="physical owners outside"):
        solve_area_master(
            ("a",), physical, ("s",), problem_sha256="p", fixed_retained_ids=("unknown",)
        )


def test_zero_cost_action_ties_are_explicit_and_do_not_gain_an_epsilon_penalty():
    physical = area((("a",), ("b",)), (0, 1e-11))
    result = solve_area_master(("a", "b"), physical, ("s",), problem_sha256="p")
    assert result["next_area_m2"] >= 0
    assert result["mip_dual_bound_m2"] <= result["next_area_m2"] + 1e-8
    assert result["proposal_tie_break_policy"] == "solver_arbitrary;no_epsilon_count_penalty"
    assert not result["necessary_demolition_identities_proven"]


def test_area_cap_restricts_remaining_domain_and_never_claims_global_infeasibility():
    physical = area((("a",), ("b",)), (5, 8))
    cache = {("p", (), "s"): ProtectionCheck("failed", {})}
    bounded = solve_area_master(
        ("a", "b"),
        physical,
        ("s",),
        problem_sha256="p",
        cache=cache,
        maximum_cleared_area_m2=4,
    )
    assert bounded["remaining_scope_empty"] and bounded["status"] == "infeasible"
    assert bounded["infeasibility_scope"] == "declared_area_cap"
    assert bounded["maximum_cleared_area_m2"] == 4
    assert not bounded["physical_optimality_proven"]
    unbounded = solve_area_master(("a", "b"), physical, ("s",), problem_sha256="p", cache=cache)
    assert unbounded["next_ids"] == ["a"] and unbounded["next_area_m2"] == 5
    assert bounded["master_formulation_sha256"] != unbounded["master_formulation_sha256"]


@pytest.mark.parametrize("status", ["passed", "failed", "unknown"])
def test_out_of_cap_cache_is_rejected_for_every_status(status):
    physical = area((("a",),), (5,))
    with pytest.raises(ValueError, match="outside the declared area cap"):
        solve_area_master(
            ("a",),
            physical,
            ("s",),
            problem_sha256="p",
            maximum_cleared_area_m2=4,
            cache={("p", ("a",), "s"): ProtectionCheck(status, {})},
        )


def test_cap_uses_same_physical_fsum_as_plan_guard_at_float_boundary():
    physical = area((("a",), ("b",), ("c",)), (0.1, 0.2, 0.3))
    scenarios = ("a-fire", "b-fire", "c-fire")
    cache = {
        ("p", (), scenario): certificate((), (identifier,), scenario=scenario, source=identifier)
        for scenario, identifier in zip(scenarios, ("a", "b", "c"), strict=True)
    }
    result = solve_area_master(
        ("a", "b", "c"),
        physical,
        scenarios,
        problem_sha256="p",
        cache=cache,
        maximum_cleared_area_m2=physical(("a", "b", "c")),
    )
    assert result["next_ids"] == ["a", "b", "c"]
    assert result["next_area_m2"] == 0.6
