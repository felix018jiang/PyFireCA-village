"""Thin widths merge only identical D/G actions, never merely equal costs."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def preparation(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "thin_hull_preparation", scripts / "prepare_heritage_thin_hull_catalog.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def row(name, width, roofs, grass, cost=100):
    return {
        "plan_id": name,
        "nominal_ring_width_m": width,
        "demolished_ids": roofs,
        "cleared_physical_cell_ids": grass,
        "C_construction_action_area_m2": cost,
    }


def test_all_five_identical_actions_create_one_case_family_with_width_aliases(preparation):
    plans = [row(f"w{w}", w, ["M"], [5]) for w in preparation.WIDTHS]
    before = deepcopy(plans)
    merged = preparation.deduplicate_actions(plans)
    assert len(merged) == 1
    assert merged[0]["nominal_ring_width_m"] == 0.01
    assert merged[0]["equivalent_widths_m"] == list(preparation.WIDTHS)
    assert plans == before


def test_equal_cost_does_not_authorize_merging_different_physical_actions(preparation):
    plans = [row("a", 0.01, ["M"], [5]), row("b", 0.5, ["U"], [5]), row("c", 1, ["M"], [6])]
    assert len(preparation.deduplicate_actions(plans)) == 3


def test_canonical_action_order_does_not_create_an_extra_physical_arm(preparation):
    merged = preparation.deduplicate_actions(
        [
            row("a", 0.01, ["M", "U"], [5, 6]),
            row("b", 0.5, ["U", "M"], [6, 5]),
        ]
    )
    assert len(merged) == 1
    assert merged[0]["equivalent_parameter_plan_ids"] == ["a", "b"]
