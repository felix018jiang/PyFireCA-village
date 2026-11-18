"""Geometric annulus controls must expose leaks and preserve action-area units."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box

from pyfireca.village.domain import VillageGridSpec


@pytest.fixture
def bands():
    script = Path(__file__).resolve().parents[2] / "scripts/prepare_heritage_all_roof_bands.py"
    spec = importlib.util.spec_from_file_location("all_roof_bands_test", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def scene(bands):
    grid = VillageGridSpec(20, 20, 5.0, 0.0, 100.0)
    roof = box(46, 46, 54, 54)
    cells = bands.parent_cell_polygons(grid)
    roof_ids = np.asarray([i for i, cell in enumerate(cells) if cell.intersection(roof).area > 0])
    geometry = list(cells)
    covered = np.full((20, 20), 25.0)
    building_index = np.full((20, 20), -1)
    for i in roof_ids:
        geometry[i] = cells[i].intersection(roof)
        covered.ravel()[i] = geometry[i].area
        building_index.ravel()[i] = 0
    parent = SimpleNamespace(
        grid=grid,
        domain_mask=np.ones((20, 20), bool),
        known_mask=np.ones((20, 20), bool),
        burnable_mask=np.ones((20, 20), bool),
        building_index=building_index,
        parameters=SimpleNamespace(covered_area_m2=covered),
    )
    base = SimpleNamespace(
        cell_id=np.arange(400),
        owners=tuple(("A",) if i in roof_ids else () for i in range(400)),
        geometry=tuple(geometry),
        node_inputs=SimpleNamespace(
            parameters=SimpleNamespace(covered_area_m2=covered),
            burnable_mask=parent.burnable_mask,
            source_mask=parent.burnable_mask.copy(),
            target_mask=parent.burnable_mask.copy(),
        ),
    )
    building = SimpleNamespace(building_id="A", geometry=roof)
    inventory = SimpleNamespace(
        buildings=(building,), by_id={"A": building}, targets=(SimpleNamespace(building_id="A"),)
    )
    return parent, base, inventory, roof_ids


def test_whole_cell_ring_has_conservative_cut_even_with_unmodeled_roof_cell_remainder(bands, scene):
    parent, base, inventory, roof_ids = scene
    plans, _, _ = bands.generate_bands(
        parent, base, inventory, seed_ids=(), radius_m=15, offsets=(0,)
    )
    plan = plans[0]
    assert not set(roof_ids).intersection(plan["cleared_physical_cell_ids"])
    assert plan["source_core_partition"]["core_ids_inside_inner"] == ["A"]
    assert plan["excluded_cell_categories_nonexclusive"]["roof_raster_cell"][
        "nominal_ring_overlap_geometry_m2"
    ] == pytest.approx(36)
    proof = plan["conservative_cut_evidence"]
    assert proof["certified_no_potential_radiation_or_contact_edge"]
    assert proof["minimum_inner_outer_physical_polygon_distance_m"] > 15
    assert proof["potential_unordered_inner_outer_pairs_within_radiation_support"] == 0
    assert plan["remaining_potential_node_positive_ring_overlap_count"] == 0


def test_unknown_burnable_corridor_is_retained_and_cannot_receive_a_cut_certificate(bands, scene):
    parent, base, inventory, _ = scene
    parent.known_mask[9, 11:16] = False
    unknown = set(9 * 20 + col for col in range(11, 16))
    plans, _, _ = bands.generate_bands(
        parent, base, inventory, seed_ids=(), radius_m=15, offsets=(0,)
    )
    plan = plans[0]
    assert not unknown.intersection(plan["cleared_physical_cell_ids"])
    assert plan["remaining_potential_node_positive_ring_overlap_count"] > 0
    assert not plan["conservative_cut_evidence"]["certified_no_potential_radiation_or_contact_edge"]
    assert (
        plan["conservative_cut_evidence"][
            "potential_unordered_inner_outer_pairs_within_radiation_support"
        ]
        > 0
    )


def test_sparse_fuel_changes_covered_loss_but_never_whole_cell_construction_charge(bands, scene):
    parent, base, inventory, _ = scene
    parent.parameters.covered_area_m2[8, 8] = 10
    known_before, covered_before = (
        parent.known_mask.copy(),
        parent.parameters.covered_area_m2.copy(),
    )
    plans, _, _ = bands.generate_bands(
        parent, base, inventory, seed_ids=(), radius_m=15, offsets=(0,)
    )
    plan = plans[0]
    assert 8 * 20 + 8 in plan["cleared_physical_cell_ids"]
    count = plan["cleared_vegetation_parent_cell_count"]
    assert plan["C_construction_action_area_m2"] == count * 25
    assert plan["cleared_vegetation_covered_fuel_area_m2"] == count * 25 - 15
    np.testing.assert_array_equal(parent.known_mask, known_before)
    np.testing.assert_array_equal(parent.parameters.covered_area_m2, covered_before)
