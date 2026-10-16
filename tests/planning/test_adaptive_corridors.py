"""Search invariants: actual protection first, reproducibility and topology."""

from dataclasses import replace

import pytest
from shapely.geometry import LineString

from pyfireca.planning.adaptive import (
    CorridorSearchConfig,
    canonical_plan,
    pareto_records,
    search_corridors,
)
from pyfireca.planning.evaluation import EvaluationResult
from pyfireca.planning.interventions.clearing import (
    ClearingPath,
    ClearingPlan,
    clearing_footprint,
    clearing_plan_from_mapping,
)


def evaluation(plan, houses, area, delay=3600):
    return EvaluationResult(
        "test",
        "case",
        "eval",
        "test",
        "train",
        plan.plan_id,
        3600,
        {
            "ignited_house_count": houses,
            "ignited_building_area_m2": houses * 100,
            "ignited_bridge_count": 0,
            "house_ignition_free_time_s": delay,
            "burning_reached_domain_edge": False,
        },
        {"cleared_area_m2": area},
        {},
        {},
    )


def run_search(oracle, **kwargs):
    seed = ClearingPlan("seed", paths=(ClearingPath(((30.0, 40.0), (70.0, 40.0)), 10),))
    return search_corridors(
        zone_id="zone",
        bounds=(0, 0, 100, 100),
        anchors=((30.0, 30.0), (50.0, 50.0), (70.0, 70.0)),
        seeds=(seed,),
        evaluate=oracle,
        **kwargs,
    )


def test_path_bends_form_a_continuous_corridor_and_round_trip():
    path = ClearingPath(((10.0, 10.0), (20.0, 10.0), (20.0, 20.0)), 4)
    plan = ClearingPlan("bent", paths=(path,))
    footprint = clearing_footprint(plan)
    assert footprint.geom_type == "Polygon"
    assert footprint.covers(LineString(path.points_m))
    assert clearing_plan_from_mapping(plan.to_mapping()) == plan
    duplicate = replace(plan, paths=(path, path))
    assert clearing_footprint(duplicate).area == pytest.approx(footprint.area)


@pytest.mark.parametrize(
    "points",
    [
        (),
        ((0.0, 0.0),),
        ((0.0, 0.0), (0.0, 0.0)),
        ((0.0, 0.0), (2.0, 2.0), (0.0, 2.0), (2.0, 0.0)),
        ((0.0, 0.0), (float("nan"), 1.0)),
    ],
)
def test_invalid_route_rejected(points):
    with pytest.raises(ValueError):
        ClearingPath(points, 2)


def test_canonical_identity_ignores_path_order_and_direction():
    p = ClearingPath(((10.0, 10.0), (30.0, 10.0)), 5)
    q = ClearingPath(((20.0, 20.0), (30.0, 30.0)), 10)
    assert canonical_plan("z", (p, q)) == canonical_plan(
        "z", (ClearingPath(q.points_m[::-1], 10), p, p)
    )
    assert canonical_plan("other", (p,)).plan_id != canonical_plan("z", (p,)).plan_id


def test_no_area_saving_can_outweigh_a_worse_fire_outcome():
    def oracle(plan):
        return [evaluation(plan, 0 if plan.paths else 1, 1e6 if plan.paths else 0)]

    result = run_search(oracle, config=CorridorSearchConfig(max_evaluations=30))
    chosen = next(r for r in result["records"] if r["plan_id"] == result["representative"])
    assert chosen["effectiveness"][0] == 0
    assert chosen["cleared_area_m2"] == 1e6
    # Zero-treatment remains a valid area/protection tradeoff.
    assert any(
        not r["plan"]["paths"] for r in result["records"] if r["plan_id"] in result["pareto_ids"]
    )


def test_equal_effect_uses_actual_area_only_in_requested_mode():
    def oracle(plan):
        return [evaluation(plan, 0, clearing_footprint(plan).area)]

    efficient = run_search(oracle, config=CorridorSearchConfig(max_evaluations=24))
    assert efficient["best_key"][-1] == 0
    assert len(efficient["effectiveness_tied_ids"]) > 1
    effect_only = run_search(
        oracle, config=CorridorSearchConfig(max_evaluations=24, objective="effectiveness_only")
    )
    assert len(effect_only["selected_ids"]) > 1


def test_seeded_search_is_reproducible_and_visits_bent_multiple_paths():
    def oracle(plan):
        # Reward shape diversity to require actual non-straight and multi-path exploration.
        bends = sum(max(0, len(p.points_m) - 2) for p in plan.paths)
        houses = max(0, 5 - len(plan.paths) - bends)
        return [evaluation(plan, houses, clearing_footprint(plan).area)]

    config = CorridorSearchConfig(max_evaluations=70)
    a, b = run_search(oracle, config=config), run_search(oracle, config=config)
    assert a == b
    assert len(a["records"]) == config.max_evaluations
    assert len({r["plan_id"] for r in a["records"]}) == len(a["records"])
    assert any(len(r["plan"]["paths"]) > 1 for r in a["records"])
    assert any(len(p["points_m"]) > 2 for r in a["records"] for p in r["plan"]["paths"])
    history = a["history"]
    keys = [(*h["best_effectiveness"], h["best_cleared_area_m2"]) for h in history]
    assert keys == sorted(keys, reverse=True)


def test_unexpected_solver_errors_are_not_treated_as_invalid_geometry():
    calls = 0

    def broken(plan):
        nonlocal calls
        calls += 1
        if calls > 2:
            raise ValueError("solver energy budget failure")
        return [evaluation(plan, 1, 100)]

    with pytest.raises(ValueError, match="solver energy budget failure"):
        run_search(broken, config=CorridorSearchConfig(max_evaluations=15))


def test_pareto_set_respects_full_effectiveness_tuple_and_area():
    def record(identity, score, area):
        return {
            "plan_id": identity,
            "status": "ok",
            "effectiveness": score,
            "cleared_area_m2": area,
        }

    records = [
        record("small", [2, 2, 200, 0, -100], 10),
        record("protective", [0, 0, 0, 0, -3600], 100),
        record("dominated", [2, 2, 200, 0, -100], 50),
    ]
    assert {r["plan_id"] for r in pareto_records(records)} == {"small", "protective"}
