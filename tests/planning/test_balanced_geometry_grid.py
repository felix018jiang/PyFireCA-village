"""Geometry-only archives must exclude unproved plans and duplicate actions."""

import importlib.util
from pathlib import Path

import pytest


@pytest.fixture
def grid(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "geometry_grid_test", scripts / "optimize_heritage_balanced_geometry.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def point(name, cost, capacity, action, *, certified=True):
    return {
        "plan_id": name,
        "C_construction_action_area_m2": cost,
        "inner_remaining_potential_covered_capacity_m2": capacity,
        "demolished_ids": [action],
        "cleared_physical_cell_ids": [],
        "inner_hull_buffer_m": 10,
        "nominal_ring_width_m": 20,
        "geometry_certified": certified,
    }


def test_archive_filters_unproved_actions_deduplicates_and_retains_tradeoff_endpoints(grid):
    plans = [
        point("cheap", 100, 200, "A"),
        point("tighter-same-action", 100, 190, "A"),
        point("middle", 120, 100, "B"),
        point("dominated", 150, 150, "C"),
        point("low-capacity", 130, 80, "D"),
        point("invalid-cheapest", 1, 1, "E", certified=False),
    ]
    canonical, frontier, top = grid.geometry_archive(plans)
    assert len(canonical) == 4
    assert {p["plan_id"] for p in frontier} == {"tighter-same-action", "middle", "low-capacity"}
    assert [p["plan_id"] for p in top] == ["tighter-same-action", "low-capacity", "middle"]
    a = next(p for p in canonical if p["demolished_ids"] == ["A"])
    assert a["equivalent_parameter_plan_ids"] == ["cheap", "tighter-same-action"]


def test_preregistered_grid_has_exactly_the_declared_51_combinations(grid):
    assert tuple(i * 2.5 for i in range(17)) == grid.OFFSETS
    assert grid.WIDTHS == (15.01, 17.5, 20.0)
    assert len({(r, w) for r in grid.OFFSETS for w in grid.WIDTHS}) == 51


def test_strict_distance_unknown_and_first_retained_source_are_separate_required_filters(grid):
    plan = {
        "conservative_cut_evidence": {
            "potential_unordered_inner_outer_pairs_within_radiation_support": 0,
            "minimum_inner_outer_physical_polygon_distance_m": 15.01,
            "radiation_support_radius_m": 15.0,
            "certified_no_potential_radiation_or_contact_edge": True,
        },
        "protected_original_roof_node_cleared_count": 0,
        "core_and_actual_source_geometries_inside_inner": True,
        "nominal_ring_outside_parent_rectangle_area_m2": 0,
        "unknown_parent_cells_intersecting_ring": 0,
        "remaining_potential_node_positive_ring_overlap_count": 0,
    }
    expected = {"fire": "S"}
    assert grid.rejection_reasons(plan, expected, expected) == []
    plan["conservative_cut_evidence"]["minimum_inner_outer_physical_polygon_distance_m"] = 15.0
    plan["unknown_parent_cells_intersecting_ring"] = 1
    reasons = grid.rejection_reasons(plan, expected, {"fire": "replacement"})
    assert set(reasons) == {
        "distance_not_strictly_above_support",
        "unknown_ring_cell",
        "source_changed",
    }
