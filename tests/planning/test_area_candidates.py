"""Geometry proposals preserve action eligibility and expose their limits."""

import pytest
from shapely.geometry import box

from pyfireca.planning.area_candidates import (
    build_cleared_area_function,
    generate_area_candidates,
)
from pyfireca.planning.heritage import BuildingInventory, BuildingObject, HeritageTarget


def inventory(areas, *, protected_ids=()):
    buildings = tuple(
        BuildingObject(i, index, "building", box(index * 100, 0, index * 100 + area, 1))
        for index, (i, area) in enumerate(areas.items())
    )
    return BuildingInventory(
        (*buildings, BuildingObject("core", len(buildings), "building", box(-10, -10, -9, -9))),
        (HeritageTarget("core", "gulou", "synthetic", "synthetic"),),
        protected_ids=protected_ids,
        candidate_ids=tuple(i for i in areas if i not in protected_ids),
        candidate_evidence="synthetic_test",
    )


def cost(areas):
    return build_cleared_area_function(
        ((i,) for i in areas), areas.values(), area_basis="physical_patch_geometry_m2"
    )


def test_shared_patch_requires_all_owners_and_background_is_not_demolition():
    area = build_cleared_area_function(
        (("a",), ("b",), ("a", "b"), ("a", "b"), ("core",), ()),
        (1, 2, 4, 6, 99, 100),
        area_basis="explicit_disjoint_geometry_projection_m2",
    )
    assert area(()) == 0
    assert area(("a",)) == 1
    assert area(("b",)) == 2
    assert area(("a", "b")) == 13
    assert area(("a", "b", "core")) == 112
    assert area.physical_patch_count == 6
    assert len(area.owner_groups) == 4
    assert area.owner_ids == {"a", "b", "core"}
    assert area.area_basis == "explicit_disjoint_geometry_projection_m2"


def test_physical_area_basis_is_explicit_and_never_silently_changed():
    geometry = build_cleared_area_function((("a",),), (4,), area_basis="geometry_m2")
    parameter = build_cleared_area_function((("a",),), (3,), area_basis="covered_area_parameter_m2")
    assert geometry(("a",)) == 4
    assert parameter(("a",)) == 3
    with pytest.raises(ValueError, match="explicit physical area basis"):
        build_cleared_area_function((), (), area_basis=" ")
    with pytest.raises(ValueError, match="equal lengths"):
        build_cleared_area_function((("a",),), (), area_basis="geometry_m2")
    with pytest.raises(ValueError, match="unique nonempty"):
        build_cleared_area_function((("a", "a"),), (1,), area_basis="geometry_m2")


def test_all_exchange_classes_are_bounded_and_seed_is_always_preserved():
    areas = {"a": 30, "b": 40, "c": 50, "d": 1, "e": 2, "f": 3, "g": 4}
    result = generate_area_candidates(
        inventory(areas), ("a", "b", "c"), cost(areas), max_generated=200, max_plans=12
    )
    assert result["plans"][0]["demolished_ids"] == ["a", "b", "c"]
    assert result["plans"][0]["source"] == "seed"
    assert {row["source"] for row in result["plans"]} == {
        "seed",
        "single_addback",
        "single_replacement",
        "double_exchange",
        "small_bundle",
    }
    assert result["returned_plan_count"] == 12
    assert result["generated_attempts_including_seed"] <= 200
    assert result["inventory_candidate_count"] == 7
    assert result["eligible_candidate_count"] == 7
    assert not result["simulation_used"]
    assert not result["global_optimality_proven"]
    assert result["full_candidate_space_unresolved"]
    following = [row["cleared_area_m2"] for row in result["plans"][1:]]
    assert following == sorted(following)
    for row in result["plans"]:
        assert len(row["demolished_ids"]) == len(set(row["demolished_ids"]))
        assert row["cleared_area_m2"] == sum(areas[i] for i in row["demolished_ids"])
        assert row["area_change_from_seed_m2"] == row["cleared_area_m2"] - 120


def test_two_small_additions_can_replace_large_seed_and_increase_count_while_reducing_area():
    areas = {"L": 100, "M": 20, "N": 20, "a": 2, "b": 3}
    result = generate_area_candidates(
        inventory(areas),
        ("L", "M", "N"),
        cost(areas),
        per_class_limits={
            "single_addback": 0,
            "single_replacement": 0,
            "double_exchange": 0,
            "small_bundle": 2,
        },
        max_bundle_size=2,
    )
    proposal = result["plans"][1]
    assert proposal["demolished_ids"] == ["M", "N", "a", "b"]
    assert proposal["removed_from_seed_ids"] == ["L"]
    assert proposal["added_to_seed_ids"] == ["a", "b"]
    assert proposal["demolished_count"] == 4
    assert proposal["cleared_area_m2"] == 45
    assert proposal["area_change_from_seed_m2"] == -95


def test_three_for_one_bundle_is_available_without_monotone_cost_pruning():
    areas = {"large": 100, "a": 1, "b": 2, "c": 3}
    result = generate_area_candidates(
        inventory(areas),
        ("large",),
        cost(areas),
        per_class_limits={
            "single_addback": 0,
            "single_replacement": 0,
            "double_exchange": 0,
            "small_bundle": 8,
        },
        max_bundle_size=3,
    )
    assert any(row["demolished_ids"] == ["a", "b", "c"] for row in result["plans"])


def test_far_small_ordinary_building_remains_available_and_sources_are_excluded():
    areas = {"seed": 10, "near": 8, "source": 0.2, "protected": 0.3, "far": 1}
    inv = inventory(areas, protected_ids=("protected",))
    result = generate_area_candidates(
        inv,
        ("seed",),
        cost(areas),
        fixed_source_ids=("source",),
        per_class_limits={
            "single_addback": 0,
            "single_replacement": 2,
            "double_exchange": 0,
            "small_bundle": 0,
        },
    )
    assert result["plans"][1]["demolished_ids"] == ["far"]
    assert "far" in result["eligible_candidate_ids"]
    assert result["excluded_protected_or_source_ids"] == ["source"]
    assert result["inventory_candidate_count"] == 4
    assert result["eligible_candidate_count"] == 3
    for row in result["plans"]:
        assert not set(row["demolished_ids"]) & {"source", "protected", "core"}
    with pytest.raises(ValueError, match="seed must contain"):
        generate_area_candidates(inv, ("source",), cost(areas), fixed_source_ids=("source",))


def test_per_class_traversal_and_output_budgets_are_reported_without_false_exhaustion_proof():
    areas = {"a": 30, "b": 40, "c": 50} | {f"new{i}": i + 1 for i in range(30)}
    result = generate_area_candidates(
        inventory(areas),
        ("a", "b", "c"),
        cost(areas),
        max_generated=13,
        max_plans=5,
    )
    assert result["generated_attempts_including_seed"] == 13
    assert result["returned_plan_count"] == 5
    assert result["generation_limited"]
    assert all(row["attempted"] <= 3 for row in result["per_class_statistics"].values())
    assert {row["source"] for row in result["plans"]} == {
        "seed",
        "single_addback",
        "single_replacement",
        "double_exchange",
        "small_bundle",
    }


def test_true_shared_area_ranks_complete_candidates_and_is_reproducible():
    areas = {"seed": 10, "a": 20, "b": 20, "c": 30}
    inv = inventory(areas)
    physical = build_cleared_area_function(
        (("seed",), ("a",), ("b",), ("a", "b"), ("c",)),
        (10, 1, 1, 100, 4),
        area_basis="physical_patch_geometry_m2",
    )
    arguments = {
        "per_class_limits": {
            "single_addback": 0,
            "single_replacement": 0,
            "double_exchange": 0,
            "small_bundle": 4,
        },
        "max_bundle_size": 2,
    }
    first = generate_area_candidates(inv, ("seed",), physical, **arguments)
    second = generate_area_candidates(inv, ("seed",), physical, **arguments)
    assert first == second
    assert first["plans"][1]["demolished_ids"] == ["a", "c"]
    assert first["plans"][1]["cleared_area_m2"] == 5
    assert first["plans"][-1]["demolished_ids"] == ["a", "b"]
    assert first["plans"][-1]["cleared_area_m2"] == 102


def test_seed_only_budget_and_empty_seed_do_not_add_unrequested_actions():
    areas = {"a": 1, "b": 2}
    limited = generate_area_candidates(inventory(areas), ("a",), cost(areas), max_generated=1)
    assert limited["returned_plan_count"] == 1
    assert limited["generated_attempts_including_seed"] == 1
    empty = generate_area_candidates(inventory(areas), (), cost(areas))
    assert empty["plans"][0]["demolished_ids"] == []
    assert empty["returned_plan_count"] == 1


@pytest.mark.parametrize("bad", [float("inf"), float("nan"), -1, True])
def test_invalid_physical_area_is_rejected(bad):
    with pytest.raises(ValueError, match="finite and nonnegative"):
        build_cleared_area_function((("a",),), (bad,), area_basis="geometry_m2")


def test_unknown_owner_or_unknown_proposal_class_cannot_hide_in_metadata():
    inv = inventory({"a": 1})
    physical = build_cleared_area_function((("missing",),), (1,), area_basis="geometry_m2")
    with pytest.raises(ValueError, match="owners must belong"):
        generate_area_candidates(inv, ("a",), physical)
    with pytest.raises(ValueError, match="unknown proposal class"):
        generate_area_candidates(inv, ("a",), cost({"a": 1}), per_class_limits={"made_up": 1})
