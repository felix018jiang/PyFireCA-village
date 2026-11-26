"""The missing factorial arms preserve physical AND costs and whole-cell actions."""

import importlib.util
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from shapely.geometry import box

from pyfireca.village.domain import VillageGridSpec


@pytest.fixture
def preparer(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "direct_ablation_preparer", scripts / "prepare_heritage_direct_action_ablation.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_missing_two_actions_are_declared_without_mutating_reference(preparer):
    thin = {
        "plan_id": preparer.THIN_PLAN,
        "demolished_ids": [*preparer.SEED, "A", "B", "C", "D", "E", "F"],
        "cleared_physical_cell_ids": list(range(12)),
    }
    original = deepcopy(thin)
    rows = preparer.ablation_rows(preparer.SEED, thin)
    assert len(rows) == 2
    assert rows[0]["demolished_ids"] == list(preparer.SEED)
    assert rows[0]["cleared_physical_cell_ids"] == list(range(12))
    assert rows[1]["demolished_ids"] == sorted(thin["demolished_ids"])
    assert rows[1]["cleared_physical_cell_ids"] == []
    assert thin == original
    thin["cleared_physical_cell_ids"][-1] = 0
    with pytest.raises(ValueError, match="D9/G12"):
        preparer.ablation_rows(preparer.SEED, thin)


def geometry():
    grid = VillageGridSpec(1, 2, 5, 0, 5)
    parent = SimpleNamespace(
        grid=grid,
        domain_mask=np.array([[True, True]]),
        known_mask=np.array([[True, True]]),
        burnable_mask=np.array([[True, True]]),
        building_index=np.array([[0, -1]]),
        parameters=SimpleNamespace(covered_area_m2=np.array([[15.0, 10.0]])),
    )
    base = SimpleNamespace(
        owners=(("A",), ("A", "B"), ("B",), ()),
        geometry=(box(0, 0, 1, 5), box(1, 0, 2, 5), box(2, 0, 3, 5), box(5, 0, 10, 5)),
        cell_id=np.array([0, 0, 0, 1]),
        node_inputs=SimpleNamespace(
            parameters=SimpleNamespace(covered_area_m2=np.array([[5.0, 5.0, 5.0, 10.0]]))
        ),
    )
    inventory = SimpleNamespace(
        by_id={
            "A": SimpleNamespace(geometry=box(0, 0, 2, 5)),
            "B": SimpleNamespace(geometry=box(1, 0, 3, 5)),
        }
    )
    return parent, base, inventory


def row(roofs=("A",), cells=(1,)):
    return {
        "plan_id": "ablation",
        "demolished_ids": list(roofs),
        "cleared_physical_cell_ids": list(cells),
    }


def test_unique_shared_AND_roof_and_whole25_construction_not_covered10(preparer):
    inputs = geometry()
    a = preparer.action_cost(*inputs, row(cells=(1, 1)), ())
    assert a["cleared_roof_geometry_union_area_m2"] == 5
    assert a["cleared_vegetation_parent_cell_count"] == 1
    assert a["cleared_vegetation_covered_fuel_area_m2"] == 10
    assert a["cleared_vegetation_ground_footprint_area_m2"] == 25
    assert a["C_construction_action_area_m2"] == 30
    b = preparer.action_cost(*inputs, row(roofs=("A", "B")), ())
    assert b["cleared_roof_geometry_union_area_m2"] == 15
    assert b["C_construction_action_area_m2"] == 40
    assert b["physical_union_recomputed_area_m2"] == 40


def test_core_source_unknown_roofcell_and_unknown_ground_are_rejected(preparer):
    inputs = geometry()
    with pytest.raises(ValueError, match="core or any actual source"):
        preparer.action_cost(*inputs, row(), {"A"})
    with pytest.raises(ValueError, match="unknown inventory"):
        preparer.action_cost(*inputs, row(roofs=("missing",)), ())
    with pytest.raises(ValueError, match="eligible whole nonroof"):
        preparer.action_cost(*inputs, row(cells=(0,)), ())
    inputs[0].known_mask[0, 1] = False
    with pytest.raises(ValueError, match="eligible whole nonroof"):
        preparer.action_cost(*inputs, row(), ())
