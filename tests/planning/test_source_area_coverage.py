"""Exhaustive coverage of every area-admissible retained-source action subset."""

from dataclasses import replace
from itertools import combinations
from math import inf, nextafter

import pytest
from shapely.geometry import box

import pyfireca.planning.source_area_coverage as coverage_module
from pyfireca.planning.area_candidates import ClearedAreaFunction, build_cleared_area_function
from pyfireca.planning.heritage import BuildingInventory, BuildingObject, content_hash
from pyfireca.planning.roof_ignition import unambiguous_roof
from pyfireca.planning.source_area_coverage import cover_source_priority_by_area


def inputs(*, immutable=()):
    # A and B overlap by 5 m2 and each have exactly 25 m2 exclusive area.
    shapes = {
        "A": box(0, 0, 6, 5),
        "B": box(5, 0, 11, 5),
        "C": box(20, 0, 25, 5),
        "D": box(30, 0, 35, 5),
    }
    inventory = BuildingInventory(
        tuple(BuildingObject(i, n, "building", g) for n, (i, g) in enumerate(shapes.items())),
        candidate_ids=tuple(i for i in shapes if i not in immutable),
        fixed_source_ids=tuple(immutable),
        candidate_evidence="synthetic_coverage_test",
    )
    area = build_cleared_area_function(
        [("A",), ("B",), ("A", "B"), ("C",), ("D",)],
        [25, 25, 5, 25, 25],
        area_basis="disjoint_synthetic_geometry_atoms",
    )
    draw = {"draw_id": "frozen", "priority_ids": ["A", "B", "C", "D"], "baseline_source_id": "A"}
    return inventory, area, draw


def subsets(values):
    for count in range(len(values) + 1):
        yield from combinations(values, count)


@pytest.mark.parametrize("bound", [0, 24.9, 25, 54.9, 55, 79.9, 80, 105, 1000])
@pytest.mark.parametrize("immutable", [(), ("B",), ("D",)])
def test_every_admissible_plan_has_its_actual_source_covered(bound, immutable):
    inventory, area, draw = inputs(immutable=immutable)
    coverage = cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, area, bound)
    for removed in subsets(inventory.candidate_ids):
        if area(removed) <= coverage.admissible_area_limit_m2:
            expected = next((i for i in draw["priority_ids"] if i not in removed), None)
            row = coverage.validate_plan(removed, cleared_area=area)
            assert row["source_id"] == expected
            if expected is not None:
                assert expected in coverage.source_ids
            else:
                assert row["status"] == "no_retained_source_unknown"
        else:
            with pytest.raises(ValueError, match=r"exceeds.*area cap"):
                coverage.validate_plan(removed)


def test_shared_and_cost_not_singleton_sum_controls_the_cutoff():
    inventory, area, draw = inputs()
    assert area(("A", "B")) == 55 > area(("A",)) + area(("B",))
    coverage = cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, area, 52)
    assert coverage.source_ids == ("A", "B")
    assert coverage.pre_removed_areas == (0, 25)
    assert coverage.stopping_prefix_area_m2 == 55
    assert coverage.stopping_removed_prefix_ids == ("A", "B")
    assert coverage.coverage_reason == "strict_prefix_area_exceeds_admissible_cap"


def test_equal_bound_is_never_pruned_and_no_closure_retains_full_priority():
    inventory, area, draw = inputs()
    equality = cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, area, 55)
    assert equality.source_ids == ("A", "B", "C")
    full = cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, area, 1000)
    assert full.source_ids == tuple(draw["priority_ids"])
    assert full.to_mapping()["no_retained_source_outcome_possible"]
    assert full.validate_plan(inventory.candidate_ids)["status"] == "no_retained_source_unknown"
    assert not full.to_mapping()["upper_bound_is_optimality_proof"]


def test_one_frozen_tolerance_is_used_for_both_prefix_and_oracle_guard():
    inventory, area, draw = inputs()
    bound = nextafter(55, -inf)
    coverage = cover_source_priority_by_area(
        draw, inventory, inventory.candidate_ids, area, bound, area_bound_tolerance_m2=1e-10
    )
    assert "C" in coverage.source_ids  # A+B are removable under the same buffered cap.
    assert coverage.validate_plan(("A", "B"))["source_id"] == "C"
    with pytest.raises(ValueError, match=r"exceeds.*area cap"):
        coverage.validate_plan(("A", "B", "C"))
    assert coverage.to_mapping()["area_bound_tolerance_m2"] == 1e-10


def test_immutable_source_stops_without_hypothetically_removing_it():
    inventory, area, draw = inputs(immutable=("B",))
    coverage = cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, area, 1000)
    assert coverage.source_ids == ("A", "B")
    assert coverage.pre_removed_areas == (0, 25)
    assert coverage.stopping_removed_prefix_ids == ("A",)
    assert coverage.coverage_reason == "fixed_retained_source_in_prefix"
    with pytest.raises(ValueError, match=r"outside.*candidate"):
        coverage.validate_plan(("B",))
    assert not coverage.to_mapping()["no_retained_source_outcome_possible"]


def test_invalid_directly_constructed_cost_and_black_box_are_rejected():
    inventory, area, draw = inputs()
    with pytest.raises(TypeError, match="physical-patch"):
        cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, lambda _: 1, 50)
    for groups in (
        ((frozenset({"A"}), -1.0),),
        ((frozenset({"A"}), inf),),
        ((frozenset({"unknown"}), 1.0),),
        ((frozenset(), 1.0),),
        ((frozenset({"A"}), 1.0), (frozenset({"A"}), 1.0)),
    ):
        malformed = ClearedAreaFunction(groups, "explicit", len(groups))
        with pytest.raises(ValueError):
            cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, malformed, 50)
    malformed = ClearedAreaFunction(area.owner_groups, "explicit", 0)
    with pytest.raises(ValueError, match="physical patch count"):
        cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, malformed, 50)


@pytest.mark.parametrize(
    "bound,tolerance",
    [(inf, 0), (float("nan"), 0), (-1, 0), (10, -1), (10, inf), (1.7e308, 1.7e308)],
)
def test_nonfinite_or_negative_area_domain_is_rejected(bound, tolerance):
    inventory, area, draw = inputs()
    with pytest.raises(ValueError):
        cover_source_priority_by_area(
            draw, inventory, inventory.candidate_ids, area, bound, area_bound_tolerance_m2=tolerance
        )


def test_draw_qualification_action_universe_and_cost_hash_are_bound():
    inventory, area, draw = inputs()
    with pytest.raises(ValueError, match="unique"):
        cover_source_priority_by_area(
            {**draw, "priority_ids": ["A", "A"]}, inventory, inventory.candidate_ids, area, 50
        )
    with pytest.raises(ValueError, match="baseline source"):
        cover_source_priority_by_area(
            {**draw, "baseline_source_id": "B"}, inventory, inventory.candidate_ids, area, 50
        )
    with pytest.raises(ValueError, match="unknown inventory"):
        cover_source_priority_by_area(
            {"priority_ids": ["missing"], "baseline_source_id": "missing"},
            inventory,
            inventory.candidate_ids,
            area,
            50,
        )
    with pytest.raises(ValueError, match="insufficient"):
        cover_source_priority_by_area(
            draw, inventory, inventory.candidate_ids, area, 50, minimum_source_area_m2=26
        )
    with pytest.raises(ValueError, match="eligible"):
        cover_source_priority_by_area(draw, inventory, ["invalid-action"], area, 50)
    coverage = cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, area, 50)
    modified = build_cleared_area_function([("A",)], [30], area_basis=area.area_basis)
    with pytest.raises(ValueError, match="changed physical area"):
        coverage.validate_plan((), cleared_area=modified)
    saved = coverage.to_mapping()
    checksum = saved.pop("coverage_sha256")
    assert content_hash(saved) == checksum
    assert all(row["unambiguous_roof_area_m2"] >= 25 for row in saved["source_geometry_witnesses"])


def test_qualification_index_cache_preserves_geometry_and_binds_inventory_sha(monkeypatch):
    inventory, area, draw = inputs()
    coverage_module._QUALIFICATION_CACHE.clear()
    original_union = coverage_module.unary_union
    unions = []

    def counted_union(values):
        unions.append(len(values))
        return original_union(values)

    monkeypatch.setattr(coverage_module, "unary_union", counted_union)
    first = cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, area, 50)
    initial_calls = len(unions)
    assert initial_calls == 2  # Only the two overlapping source roofs require differences.
    again = cover_source_priority_by_area(draw, inventory, inventory.candidate_ids, area, 50)
    assert len(unions) == initial_calls
    assert first.to_mapping() == again.to_mapping()  # Cache warmth never changes a proof hash.
    for source, source_area, digest in first.source_geometry_witnesses:
        reference = unambiguous_roof(inventory, source)
        assert source_area == reference.area
        assert digest == content_hash(reference.wkb_hex)
    other_inventory = replace(inventory, candidate_evidence="different frozen inventory")
    cover_source_priority_by_area(draw, other_inventory, other_inventory.candidate_ids, area, 50)
    assert len(unions) == initial_calls * 2
