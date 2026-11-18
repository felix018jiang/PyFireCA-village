"""Opt-in roof observations retain actual times and separate horizon closure."""

import importlib.util
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.roof_search import RoofDemolitionOracle
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.random_sources import freeze_source_protocol

ROOT = Path(__file__).resolve().parents[2]


def fixture():
    spec = importlib.util.spec_from_file_location(
        "roof_extinction_fixture", ROOT / "examples/research/heritage/synthetic_case.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_engine_closed_prefix_is_passed_without_claiming_elapsed_horizon():
    bundle, transport, inventory, scenario = fixture().make_case(radius=0.1)
    parameters = bundle.request.parameters
    duration = float(
        (parameters.growth_time_s + parameters.steady_time_s + parameters.decay_time_s).max()
    )
    scenario = replace(scenario, horizon_s=duration + 20)
    engine = RoofHeritageEvaluationEngine(
        bundle, transport, inventory, source_policy="declared_fixed_sources"
    )
    full_record, _, full = engine.simulate(scenario, BuildingDemolitionPlan("base"))
    closed, _, prefix = engine.simulate(
        scenario, BuildingDemolitionPlan("base"), stop_at_extinction=True
    )
    assert closed.metrics["protection_status"] == "passed"
    assert prefix.final_state.time_s < scenario.horizon_s
    assert not closed.diagnostics["reached_requested_horizon"]
    assert closed.diagnostics["protection_horizon_closed"]
    assert closed.diagnostics["effective_goal_closed"] and closed.diagnostics["extinction_closed"]
    assert (
        closed.diagnostics["temporal_closure_certificate"]["actual_stop_time_s"]
        == prefix.final_state.time_s
    )
    assert full_record.diagnostics["reached_requested_horizon"]
    assert "temporal_closure_certificate" not in full_record.diagnostics
    assert closed.evaluation_sha256 != full_record.evaluation_sha256
    assert closed.metrics["core_outcomes"] == full_record.metrics["core_outcomes"]
    for field in (
        "ignition_time_s",
        "burnout_time_s",
        "fuel_remaining_kg",
        "heated_water_remaining_kg",
        "absorbed_radiation_J",
    ):
        np.testing.assert_array_equal(
            getattr(full.final_state, field), getattr(prefix.final_state, field)
        )


def test_first_core_failure_remains_failure_and_does_not_claim_full_fire_closure():
    bundle, transport, inventory, scenario = fixture().make_case(
        radius=8, ignition_temperature_K=350
    )
    engine = RoofHeritageEvaluationEngine(
        bundle, transport, inventory, source_policy="declared_fixed_sources"
    )
    record, _, result = engine.simulate(
        scenario,
        BuildingDemolitionPlan("base"),
        stop_at_extinction=True,
        stop_at_first_core_ignition=True,
    )
    assert record.metrics["protection_status"] == "failed"
    assert result.final_state.time_s < scenario.horizon_s
    assert not record.diagnostics["protection_horizon_closed"]
    assert record.diagnostics["temporal_closure_certificate"] is None


def test_oracle_stop_option_is_opt_in_hashed_and_carries_temporal_closure_evidence():
    from shapely.geometry import box

    bundle, transport, inventory, _ = fixture().make_case(
        radius=0.1,
        geometries={
            "S": box(2, 8, 7, 13),
            "M": box(7, 8, 9, 10),
            "G": box(9, 8, 11, 10),
            "F": box(9, 6, 11, 8),
            "U": box(16, 12, 18, 14),
        },
    )
    parameters = bundle.request.parameters
    horizon = (
        float((parameters.growth_time_s + parameters.steady_time_s + parameters.decay_time_s).max())
        + 20
    )
    protocol = freeze_source_protocol(
        inventory, seed=1, draws_per_stratum=1, area_m2=25, near_distance_m=15
    )
    args = {
        "candidate_ids": inventory.candidate_ids,
        "fires": [{"fire_id": "one", "protocol": protocol}],
        "code_version": "extinction-option-test",
        "horizon_s": horizon,
    }
    legacy = RoofDemolitionOracle(bundle, transport, inventory, **args)
    explicit_false = RoofDemolitionOracle(
        bundle, transport, inventory, **args, stop_at_extinction=False
    )
    closed = RoofDemolitionOracle(bundle, transport, inventory, **args, stop_at_extinction=True)
    assert legacy.problem_sha256 == explicit_false.problem_sha256
    assert "stop_at_extinction" not in legacy.problem
    assert closed.problem_sha256 != legacy.problem_sha256
    assert closed.problem["stop_at_extinction"]
    check = closed((), "one-h1")
    assert check.status == "passed"
    assert not check.evidence["reached_horizon"]
    assert check.evidence["effective_goal_closed"]
    assert check.evidence["extinction_closed"]
    assert (
        check.evidence["temporal_closure_certificate"]["actual_stop_time_s"]
        == check.evidence["simulated_until_s"]
    )
