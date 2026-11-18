"""Narrow arms remain candidates even when a geometric isolation test fails."""

import importlib.util
from copy import deepcopy
from pathlib import Path

import pytest


@pytest.fixture
def preparation(monkeypatch):
    scripts = Path(__file__).resolve().parents[2] / "scripts"
    monkeypatch.syspath_prepend(str(scripts))
    spec = importlib.util.spec_from_file_location(
        "narrow_hull_preparation", scripts / "prepare_heritage_narrow_hull_catalog.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def inputs(preparation):
    plans = [
        {
            "plan_id": f"narrow-{width}",
            "nominal_ring_width_m": width,
            "inner_hull_buffer_m": 0,
            "demolished_ids": ["M"],
            "cleared_physical_cell_ids": [n],
            "protected_original_roof_node_cleared_count": 0,
            "conservative_cut_evidence": {
                "certified_no_potential_radiation_or_contact_edge": False,
                "potential_unordered_inner_outer_pairs_within_radiation_support": 7,
                "minimum_inner_outer_physical_polygon_distance_m": width,
            },
        }
        for n, width in enumerate(preparation.WIDTHS)
    ]
    fires = {"one": {"protocol": {"draws": [{"priority_ids": ["S", "M", "U"]}]}}}
    return plans, fires, {"one": "S"}


def test_nonisolated_bands_are_untested_candidates_and_reference_is_not_rescheduled(preparation):
    plans, fires, expected = inputs(preparation)
    before = deepcopy(plans)
    rows = preparation.checked_narrow_rows(plans, fires, expected)
    assert len(rows) == 5
    assert all("15.01" not in row["plan_id"] for row in rows)
    assert all("not_fire_safety_certified" in row["ground_evidence"] for row in rows)
    assert plans == before


@pytest.mark.parametrize("field", ["source", "core"])
def test_source_or_core_removal_still_rejects_a_narrow_arm(preparation, field):
    plans, fires, expected = inputs(preparation)
    if field == "source":
        plans[0]["demolished_ids"].append("S")
    else:
        plans[0]["protected_original_roof_node_cleared_count"] = 1
    with pytest.raises(ValueError, match="source or clears a core"):
        preparation.checked_narrow_rows(plans, fires, expected)


def test_unregistered_width_set_cannot_silently_replace_the_five_candidates(preparation):
    plans, fires, expected = inputs(preparation)
    plans[-1]["nominal_ring_width_m"] = 15.01
    with pytest.raises(ValueError, match="exact preregistered"):
        preparation.checked_narrow_rows(plans, fires, expected)
