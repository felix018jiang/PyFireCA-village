"""Village-independent ring selection preserves the predefined comparison arm."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def preparation(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "village_joint_geometry_preparation", scripts / "prepare_heritage_village_joint_geometry.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rows():
    return [
        {
            "plan_id": f"b{offset:g}-w{width:g}",
            "inner_hull_buffer_m": offset,
            "nominal_ring_width_m": width,
            "C_construction_action_area_m2": 1000 + index,
            "inner_remaining_potential_covered_capacity_m2": 2000 + index,
            "demolished_ids": [str(index)],
            "cleared_physical_cell_ids": [index],
            "geometry_certified": True,
        }
        for index, (offset, width) in enumerate(
            (offset, width)
            for width in (15.01, 17.5, 20)
            for offset in (i * 2.5 for i in range(17))
        )
    ]


def test_minimum_C_is_not_hardcoded_to_batuan_parameters(preparation):
    plans = rows()
    winner = next(
        p for p in plans if p["inner_hull_buffer_m"] == 20 and p["nominal_ring_width_m"] == 17.5
    )
    winner["C_construction_action_area_m2"] = 500
    before = deepcopy(plans)
    selected, _, _ = preparation.choose_two(plans)
    assert selected[0]["plan_id"] == winner["plan_id"]
    assert selected[1]["inner_hull_buffer_m"] == 10
    assert selected[1]["nominal_ring_width_m"] == 20
    selected[1]["demolished_ids"].append("changed")
    assert plans == before


def test_uncertified_cheap_geometry_cannot_become_the_selected_arm(preparation):
    plans = rows()
    plans[0].update(C_construction_action_area_m2=0, geometry_certified=False)
    selected, _, _ = preparation.choose_two(plans)
    assert selected[0]["C_construction_action_area_m2"] == 1001


def test_full_grid_and_certified_control_are_required(preparation):
    with pytest.raises(ValueError, match="all 51"):
        preparation.choose_two(rows()[:-1])
    plans = rows()
    next(p for p in plans if p["inner_hull_buffer_m"] == 10 and p["nominal_ring_width_m"] == 20)[
        "geometry_certified"
    ] = False
    with pytest.raises(ValueError, match="b10/w20 control"):
        preparation.choose_two(plans)


def test_identical_minimum_and_control_are_labeled_without_inventing_distinct_actions(preparation):
    plans = rows()
    control = next(
        p for p in plans if p["inner_hull_buffer_m"] == 10 and p["nominal_ring_width_m"] == 20
    )
    control["C_construction_action_area_m2"] = 0
    selected, _, _ = preparation.choose_two(plans)
    assert selected[0]["plan_id"] != selected[1]["plan_id"]
    assert selected[0]["demolished_ids"] == selected[1]["demolished_ids"]
