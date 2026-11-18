"""Exhaustive small-action check of the protection-only geometric minimum."""

import itertools
from pathlib import Path
from types import SimpleNamespace as NS

import numpy as np
import pytest
from shapely.geometry import box
from shapely.ops import unary_union


@pytest.fixture
def physical_case(monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "scripts"))
    polygons = [
        box(0, 0, 1, 1),
        box(3, 0, 4, 1),
        box(20, 0, 21, 1),
        box(1, 1, 6, 6),
        box(40, 0, 45, 5),
        box(15, 0, 16, 1),
    ]
    owners = [("core",), ("a", "b"), ("a",), (), (), ("c",)]
    ones = np.ones((1, len(owners)), bool)
    physics = NS(
        absorptivity=ones * 0.5,
        emissivity=ones * 0.8,
        ambient_heat_transfer_coefficient_W_m2_K=ones * 4,
        backing_conductance_W_m2_K=ones * 2,
        ignition_temperature_K=ones * 550,
    )
    parent = NS(
        firebrands=None,
        domain_mask=ones,
        known_mask=ones,
        burnable_mask=ones,
        building_index=np.array([[0, 1, 1, -1, -1, 1]]),
        parameters=NS(covered_area_m2=np.array([[1, 1, 1, 3, 6, 1]])),
        grid=NS(cell_area_m2=25),
        scenario=NS(ambient_temperature_K=300.0),
    )
    layout = NS(
        physical_request=parent,
        node_inputs=NS(burnable_mask=ones, parameters=physics),
        cell_id=np.arange(len(owners)),
        owners=owners,
        geometry=polygons,
    )
    inventory = NS(targets=[NS(building_id="core")], candidate_ids=("a", "b", "c"))
    return layout, inventory


def test_minimum_matches_exhaustive_actions_at_inclusive_cutoff(physical_case):
    from check_heritage_core_separation import minimum_separation

    layout, inventory = physical_case
    result = minimum_separation(layout, inventory, 2.0)
    assert result["roof_actions"] == ["a", "b"]
    assert result["ground_parent_actions"] == [3]
    costs = []
    for bits in itertools.product([False, True], repeat=5):
        actions = {key for key, flag in zip(["a", "b", "c", 3, 4], bits, strict=True) if flag}
        cleared = [
            set(ids) <= actions if ids else cell in actions
            for cell, ids in enumerate(layout.owners)
        ]
        if all(
            cleared[i] or g.distance(layout.geometry[0]) > 2.0
            for i, g in enumerate(layout.geometry[1:], start=1)
        ):
            pieces = [g for g, f in zip(layout.geometry, cleared, strict=True) if f]
            costs.append(unary_union(pieces).area)
    assert result["minimum_construction_area_m2"] == min(costs) == 27
    assert result["remaining_cross_support_pair_count"] == 0
    assert result["remaining_external_burnable_parent_patch_count"] == 2


def test_unremovable_coowner_blocks_certificate(physical_case):
    from check_heritage_core_separation import minimum_separation

    layout, inventory = physical_case
    inventory.candidate_ids = ("a", "c")
    result = minimum_separation(layout, inventory, 2.0)
    assert result == {"minimum_separation_feasible": False, "unremovable_required_patch_count": 1}
