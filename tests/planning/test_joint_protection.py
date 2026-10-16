"""Joint structure protection, strict shape comparison and isolated sensitivity."""

from dataclasses import replace

import numpy as np
import pytest

from pyfireca.planning.adaptive import (
    CorridorSearchConfig,
    canonical_plan,
    matches_family,
    rank_key,
    search_corridors,
)
from pyfireca.planning.effectiveness import joint_protection_score
from pyfireca.planning.evaluation import EvaluationResult
from pyfireca.planning.interventions.clearing import ClearingPath, clearing_footprint
from pyfireca.planning.sensitivity import ignition_rise_sensitivity
from pyfireca.village.workflow_v3 import V3CaseBundle


def result(plan, houses=0, bridges=0, horizon=5400):
    return EvaluationResult(
        "test",
        "case",
        "eval",
        "test",
        "s",
        plan.plan_id,
        horizon,
        {
            "ignited_house_count": houses,
            "ignited_bridge_count": bridges,
            "ignited_building_area_m2": houses * 100 + bridges * 50,
            "house_ignition_free_time_s": horizon if not houses else 600,
            "building_first_ignitions": [] if not houses + bridges else [{"time_s": 600}],
            "burning_reached_domain_edge": False,
        },
        {"cleared_area_m2": clearing_footprint(plan).area},
        {},
        {},
    )


def test_bridge_ignition_cannot_be_hidden_by_zero_house_loss_or_small_area():
    plan = canonical_plan("z", ())
    safe = joint_protection_score([result(plan)])
    unsafe = joint_protection_score([result(plan, bridges=1)])
    assert safe < unsafe
    assert rank_key(
        {"effectiveness": safe, "cleared_area_m2": 1e9}, "joint_protection_then_area"
    ) < rank_key({"effectiveness": unsafe, "cleared_area_m2": 0}, "joint_protection_then_area")
    assert joint_protection_score([result(plan), result(plan, bridges=1)])[0] == 1


def test_protection_goal_requires_same_horizon_and_valid_domain():
    plan = canonical_plan("z", ())
    with pytest.raises(ValueError, match="same horizon"):
        joint_protection_score([result(plan), result(plan, horizon=3600)])
    bad = replace(
        result(plan), metrics={**result(plan).metrics, "burning_reached_domain_edge": True}
    )
    with pytest.raises(ValueError, match="crop-edge"):
        joint_protection_score([bad])


@pytest.mark.parametrize("family", ["straight", "polyline", "multiple"])
def test_each_strict_family_is_actually_optimized_reproducibly(family):
    a = ClearingPath(((20.0, 40.0), (80.0, 40.0)), 10)
    b = ClearingPath(((20.0, 60.0), (80.0, 60.0)), 5)
    bent = ClearingPath(((20.0, 40.0), (50.0, 50.0), (80.0, 40.0)), 10)
    seed = canonical_plan("z", {"straight": (a,), "polyline": (bent,), "multiple": (a, b)}[family])
    config = CorridorSearchConfig(
        max_evaluations=40,
        geometry_family=family,
        max_paths=3 if family == "multiple" else 1,
        max_vertices=2 if family == "straight" else 5,
        objective="joint_protection_then_area",
    )

    def run():
        return search_corridors(
            zone_id="z",
            bounds=(0, 0, 100, 100),
            anchors=((50.0, 50.0),),
            seeds=(seed,),
            config=config,
            evaluate=lambda p: [result(p)],
        )

    first = run()
    assert first == run()
    selected = next(r for r in first["records"] if r["plan_id"] == first["representative"])
    assert matches_family(
        canonical_plan(
            "z",
            tuple(
                ClearingPath(tuple(map(tuple, p["points_m"])), p["width_m"])
                for p in selected["plan"]["paths"]
            ),
        ),
        family,
    )
    assert len(first["records"]) == 40
    assert selected["cleared_area_m2"] < clearing_footprint(seed).area


def test_collinear_extra_node_is_not_a_polyline_comparison():
    plan = canonical_plan("z", (ClearingPath(((0.0, 0.0), (1.0, 0.0), (2.0, 0.0)), 1),))
    assert not matches_family(plan, "polyline")


def test_small_sensitivity_changes_only_burnable_ignition_temperature_rise():
    # Full native array fixture avoids an artificial replacement of the solver model.
    import importlib.util
    from pathlib import Path

    root = Path(__file__).parents[2]
    spec = importlib.util.spec_from_file_location(
        "sensitivity_fixture", root / "examples/village/vegetation_v3/audit_spread.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    request = module.make_request(("tree_equivalent", "building", "bare_or_road"), end_time_s=600)
    mask = request.burnable_mask
    bundle = V3CaseBundle(request, {}, {}, {})
    original = request.parameters.ignition_temperature_K.copy()
    changed = ignition_rise_sensitivity(bundle)
    ambient = request.scenario.ambient_temperature_K
    np.testing.assert_allclose(
        changed.request.parameters.ignition_temperature_K[mask] - ambient,
        0.95 * (original[mask] - ambient),
    )
    assert changed.request.parameters.ignition_temperature_K[0, 2] == original[0, 2]
    np.testing.assert_array_equal(request.parameters.ignition_temperature_K, original)
    for name in request.parameters.__dataclass_fields__:
        if name != "ignition_temperature_K":
            np.testing.assert_array_equal(
                getattr(request.parameters, name), getattr(changed.request.parameters, name)
            )
    assert "ignition_sensitivity" not in bundle.metadata
    assert not changed.metadata["ignition_sensitivity"]["empirically_calibrated"]
    with pytest.raises(ValueError):
        ignition_rise_sensitivity(bundle, 1.05)
