"""Allocation diversity and failure visibility under a fixed query budget."""

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pytest

from pyfireca.planning.adaptive import CorridorSearchConfig
from pyfireca.planning.evaluation import EvaluationResult
from pyfireca.planning.interventions.clearing import ClearingPath, ClearingPlan
from pyfireca.planning.joint import joint_initial_plans
from pyfireca.planning.joint_diverse import allocation_beam, optimize_diverse_joint
from pyfireca.village.domain import VillageGridSpec


def example():
    grid = VillageGridSpec(30, 30, 2, 0, 60)
    mask = np.ones(grid.shape, bool)
    left = np.indices(grid.shape)[1] < 15
    engine = SimpleNamespace(
        bundle=SimpleNamespace(
            request=SimpleNamespace(
                grid=grid, burnable_mask=mask, building_index=np.full(grid.shape, -1)
            )
        ),
        zones={"outer_proxy": left, "inner_proxy": ~left},
    )
    a = ClearingPlan(
        "out", zone_id="outer_proxy", paths=(ClearingPath(((12.0, 20.0), (12.0, 40.0)), 8.0),)
    )
    b = ClearingPlan(
        "in", zone_id="inner_proxy", paths=(ClearingPath(((42.0, 20.0), (42.0, 40.0)), 8.0),)
    )
    config = CorridorSearchConfig(
        max_evaluations=32,
        objective="joint_protection_then_area",
        initial_step_m=2,
        min_step_m=0.5,
        width_min_m=1,
        width_max_m=12,
        length_min_m=5,
        length_max_m=30,
        max_paths=3,
    )
    return engine, joint_initial_plans(a, b), config


def oracle(plan):
    zones = {
        r: sum(p.width_m * 20 for c in plan.components if c.zone_id == r for p in c.paths)
        for r in ["outer_proxy", "inner_proxy"]
    }
    loss = int(sum(zones.values()) < 160)
    metrics = {
        "ignited_house_count": loss,
        "ignited_bridge_count": 0,
        "ignited_building_area_m2": float(loss),
        "house_ignition_free_time_s": 100 if loss else 5400,
        "building_first_ignitions": [{"time_s": 100}] if loss else [],
        "burning_reached_domain_edge": False,
    }
    return [
        EvaluationResult(
            "test",
            "case",
            plan.plan_id,
            "test",
            "design",
            plan.plan_id,
            5400,
            metrics,
            {"cleared_area_m2": sum(zones.values()), "zone_areas_m2": zones},
            {"reached_requested_horizon": True},
            {},
        )
    ]


def test_worse_mixed_parents_survive_beside_best_single_side():
    rows = [
        {
            "plan_id": kind,
            "status": "ok",
            "allocation_stratum": kind,
            "effectiveness": [score],
            "cleared_area_m2": area,
        }
        for kind, score, area in [
            ("inner_only", 0, 100),
            ("outer_only", 0, 200),
            ("mixed_1", 1, 300),
        ]
    ]
    assert {r["allocation_stratum"] for r in allocation_beam(rows, 1)} == {
        "inner_only",
        "outer_only",
        "mixed_1",
    }


def test_search_replays_unique_budget_and_uses_joint_moves():
    e, seeds, c = example()
    calls = []

    def evaluate(p):
        calls.append(p.plan_id)
        return oracle(p)

    a = optimize_diverse_joint(
        engine=e, seeds=seeds, evaluate=evaluate, config=c, scenario_ids=("design",)
    )
    b = optimize_diverse_joint(
        engine=e, seeds=seeds, evaluate=oracle, config=c, scenario_ids=("design",)
    )
    assert a["records"] == b["records"]
    assert len(calls) == len(set(calls)) == a["unique_requests"] <= 32
    assert a["selected"]["protection_goal_met"]
    assert any(r["operator"] == "width_exchange" for r in a["records"])
    assert "inner_only" in a["retained_strata"] and "outer_only" in a["retained_strata"]


def test_incomplete_zero_loss_never_becomes_a_selected_plan():
    e, seeds, c = example()

    def broken(p):
        row = oracle(p)[0]
        return [
            replace(
                row,
                metrics={**row.metrics, "ignited_house_count": 0},
                diagnostics={"reached_requested_horizon": False},
            )
        ]

    with pytest.raises(ValueError, match="incomplete zero-loss"):
        optimize_diverse_joint(
            engine=e, seeds=seeds, evaluate=broken, config=c, scenario_ids=("design",)
        )


def test_unexpected_oracle_failure_is_never_hidden_as_geometry_rejection():
    e, seeds, c = example()
    count = 0

    def broken(p):
        nonlocal count
        count += 1
        if count > len(seeds):
            raise ValueError("unexpected solver failure")
        return oracle(p)

    with pytest.raises(ValueError, match="unexpected solver failure"):
        optimize_diverse_joint(
            engine=e, seeds=seeds, evaluate=broken, config=c, scenario_ids=("design",)
        )


def test_zero_area_budget_cannot_return_an_overbudget_solution():
    e, seeds, c = example()
    r = optimize_diverse_joint(
        engine=e, seeds=seeds, evaluate=oracle, config=c, scenario_ids=("design",), area_cap_m2=0
    )
    assert r["selected"] is None
    assert all(x["cleared_area_m2"] == 0 for x in r["records"] if x["status"] == "ok")


@pytest.mark.parametrize("cap", [float("nan"), float("inf"), -1, True])
def test_invalid_area_caps_fail_before_any_search(cap):
    e, seeds, c = example()
    with pytest.raises((ValueError, TypeError)):
        optimize_diverse_joint(
            engine=e,
            seeds=seeds,
            evaluate=oracle,
            config=c,
            scenario_ids=("design",),
            area_cap_m2=cap,
        )
