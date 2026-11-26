"""Compact rings remove complete shared roof owners and bind finite sources."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box
from test_all_roof_bands import bands as bands
from test_all_roof_bands import scene as scene


@pytest.fixture
def compact(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "compact_roof_bands_test", scripts / "prepare_heritage_balanced_roof_bands.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def shared_scene(scene):
    parent, base, inventory, _ = scene
    geometries, owners = list(base.geometry), list(base.owners)
    source, shared = box(56, 46, 59, 49), box(70, 45, 95, 50)
    for identifier, polygon in (("S", source), ("B+C", shared)):
        for node, old in enumerate(base.geometry):
            overlap = old.intersection(polygon)
            if overlap.area > 0:
                owners[node] = tuple(identifier.split("+"))
                geometries[node] = overlap
                parent.parameters.covered_area_m2.ravel()[node] = overlap.area
                parent.building_index.ravel()[node] = 1
    base.owners, base.geometry = tuple(owners), tuple(geometries)
    additions = [
        SimpleNamespace(building_id=i, geometry=g)
        for i, g in (("S", source), ("B", shared), ("C", shared))
    ]
    inventory.buildings = (*inventory.buildings, *additions)
    inventory.by_id.update({b.building_id: b for b in additions})
    return parent, base, inventory


def test_ring_removes_all_shared_owners_and_entire_buildings_beyond_the_band(compact, shared_scene):
    parent, base, inventory = shared_scene
    plans, _, _ = compact.generate_balanced_bands(
        parent,
        base,
        inventory,
        seed_ids=(),
        actual_source_ids=("S",),
        radius_m=15,
        offsets=(10,),
    )
    plan = plans[0]
    assert plan["demolished_ids"] == ["B", "C"]
    assert plan["fully_cleared_ids"] == ["B", "C"]
    assert plan["cleared_roof_geometry_union_area_m2"] == pytest.approx(125)
    assert plan["cleared_roof_physical_node_count"] == 5
    anchors = inventory.by_id["A"].geometry.union(inventory.by_id["S"].geometry).convex_hull
    ring = anchors.buffer(30, quad_segs=64).difference(anchors.buffer(10, quad_segs=64))
    assert inventory.by_id["B"].geometry.intersection(ring).area < 125
    assert plan["protected_original_roof_node_cleared_count"] == 0
    assert plan["core_and_actual_source_geometries_inside_inner"]
    assert plan["conservative_cut_evidence"]["certified_no_potential_radiation_or_contact_edge"]


def test_finite_priority_receipt_must_recompute_sources_and_reject_no_survivor(compact):
    fires = {"fire": {"protocol": {"draws": [{"priority_ids": ["D", "S", "B"]}]}}}
    assert compact.finite_retained_sources(fires, ("D",)) == {"fire": "S"}
    assert compact.finite_retained_sources(fires, ("D", "B", "C")) == {"fire": "S"}
    assert compact.finite_retained_sources(fires, ("D", "S")) != {"fire": "S"}
    with pytest.raises(ValueError, match="no retained ignition source"):
        compact.finite_retained_sources(fires, ("D", "S", "B"))


def test_compact_cost_is_physical_roof_union_plus_full_ground_cells_without_parent_mutation(
    compact, shared_scene
):
    parent, base, inventory = shared_scene
    before = parent.burnable_mask.copy()
    plans, _, _ = compact.generate_balanced_bands(
        parent,
        base,
        inventory,
        seed_ids=(),
        actual_source_ids=("S",),
        radius_m=15,
        offsets=(10,),
    )
    p = plans[0]
    assert p["cleared_vegetation_ground_footprint_area_m2"] == (
        25 * p["cleared_vegetation_parent_cell_count"]
    )
    assert p["C_construction_action_area_m2"] == (
        125 + p["cleared_vegetation_ground_footprint_area_m2"]
    )
    np.testing.assert_array_equal(parent.burnable_mask, before)
