"""Terminal losses close at actual extinction, including failed protection arms."""

import importlib.util
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine, RoofJointPlan
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.planning.terminal_verification import (
    audit_terminal_result,
    close_terminal_event_curves,
    evaluate_terminal_joint,
    terminal_observation_is_complete,
    terminal_summary_is_complete,
    verify_terminal_fire,
)
from pyfireca.village.solver_v3 import V3State

ROOT = Path(__file__).resolve().parents[2]


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def roof_case(**kwargs):
    fixture = load(ROOT / "examples/research/heritage/synthetic_case.py", "terminal_roof_case")
    bundle, transport, inventory, scenario = fixture.make_case(**kwargs)
    engine = RoofHeritageEvaluationEngine(
        bundle, transport, inventory, source_policy="declared_fixed_sources"
    )
    return engine, replace(scenario, horizon_s=20)


def test_closed_prefix_is_terminal_complete_without_inventing_elapsed_horizon():
    engine, scenario = roof_case(radius=0.1)
    verification = verify_terminal_fire(
        engine,
        scenario,
        BuildingDemolitionPlan("base"),
        horizon_cap_s=1500,
        reference_problem_sha256="frozen-reference",
    )
    summary = verification.summary
    assert verification.check.status == "passed"
    assert summary["terminal_complete"] and summary["fire_loss_complete"]
    assert summary["protection_feasible"]
    assert not summary["reached_requested_horizon"]
    assert summary["actual_stop_time_s"] < 1500
    assert summary["new_CA_checks"] == 1
    assert summary["model_protocol"]["max_dt_s"] == engine.bundle.request.scenario.max_dt_s
    assert summary["model_protocol"]["reference_horizon_s"] == 20
    assert summary["model_protocol"]["horizon_was_extended"]
    assert summary["model_protocol"]["horizon_change_can_change_final_clipped_timestep"]
    assert not summary["model_protocol"]["previous_objective_values_or_optimality_transferred"]
    assert not summary["model_protocol"]["stop_at_first_core_ignition"]
    assert summary["model_protocol"]["stop_at_extinction"]
    assert (
        summary["temporal_closure_certificate"]["actual_stop_time_s"]
        == summary["actual_stop_time_s"]
    )
    assert summary["metrics"]["A_ignited_physical_covered_area_m2"] == 4
    assert summary["metrics"]["N_ignited_annotation_attribution_count"] == {
        "lower": 1,
        "upper": 1,
    }
    assert summary["metrics"]["real_destroyed_building_count"] is None
    assert terminal_summary_is_complete(summary)


def test_finite_horizon_pass_with_active_fire_is_terminal_unknown():
    engine, scenario = roof_case(radius=0.1)
    verification = verify_terminal_fire(
        engine, scenario, BuildingDemolitionPlan("base"), horizon_cap_s=20
    )
    assert verification.record.metrics["protection_status"] == "passed"
    assert verification.summary["reached_requested_horizon"]
    assert verification.check.status == "unknown"
    assert not verification.summary["terminal_complete"]
    assert not verification.summary["protection_feasible"]
    assert not verification.summary["fire_loss_complete"]
    assert verification.summary["temporal_closure_certificate"] is None
    assert "active_burning_nodes_remain" in verification.summary["closure_rejection_reasons"]
    assert not terminal_summary_is_complete(verification.summary)


def test_failed_core_arm_continues_to_extinction_and_complete_losses():
    engine, scenario = roof_case(radius=8, ignition_temperature_K=350)
    plan = BuildingDemolitionPlan("base")
    partial_record, _, partial = engine.simulate(
        replace(scenario, horizon_s=4000), plan, stop_at_first_core_ignition=True
    )
    assert partial_record.metrics["core_joint_failure_observed"]
    verification = verify_terminal_fire(engine, scenario, plan, horizon_cap_s=4000)
    assert verification.check.status == "failed"
    assert verification.summary["terminal_complete"]
    assert verification.summary["fire_loss_complete"]
    assert not verification.summary["protection_feasible"]
    assert verification.summary["actual_stop_time_s"] > partial.final_state.time_s
    assert verification.summary["metrics"]["fire_depleted_pyrolysable_fuel_kg"] > float(
        (partial.initial_fuel_kg - partial.final_state.fuel_remaining_kg).sum()
    )
    assert verification.fire_loss["fire_extinguished_at_observation"]


def test_existing_saved_boundary_can_be_certified_without_retroactive_stop_or_CA():
    engine, scenario = roof_case(radius=0.1)
    scenario = replace(scenario, horizon_s=1200)
    plan = BuildingDemolitionPlan("base")
    record, layout, result = engine.simulate(scenario, plan)
    verified = audit_terminal_result(
        engine, scenario, plan, record, layout, result, contact=engine.last_contact
    )
    assert verified.summary["terminal_complete"]
    assert not verified.summary["engine_certificate_was_present"]
    assert verified.summary["actual_stop_time_s"] == 1200
    assert verified.summary["new_CA_checks"] == 0
    assert not verified.summary["model_protocol"]["stop_at_extinction"]
    assert verified.summary["temporal_closure_certificate"]["last_burnout_event_time_s"] < 1200
    assert verified.summary["temporal_closure_certificate"]["actual_stop_time_s"] == 1200


def test_certificate_and_request_state_identity_are_rechecked():
    engine, scenario = roof_case(radius=0.1)
    plan = BuildingDemolitionPlan("base")
    verified = verify_terminal_fire(engine, scenario, plan, horizon_cap_s=1500)
    terminal_scenario = replace(scenario, horizon_s=1500)
    bad_diagnostics = deepcopy(verified.record.diagnostics)
    bad_diagnostics["temporal_closure_certificate"]["actual_stop_time_s"] -= 1
    bad_record = replace(verified.record, diagnostics=bad_diagnostics)
    with pytest.raises(ValueError, match="certificate differs"):
        audit_terminal_result(
            engine,
            terminal_scenario,
            plan,
            bad_record,
            verified.layout,
            verified.result,
            contact=engine.last_contact,
        )
    bad_record = replace(
        verified.record,
        diagnostics={**verified.record.diagnostics, "physical_request_sha256": "changed"},
    )
    with pytest.raises(ValueError, match="request or identity"):
        audit_terminal_result(
            engine,
            terminal_scenario,
            plan,
            bad_record,
            verified.layout,
            verified.result,
            contact=engine.last_contact,
        )


def test_joint_metrics_separate_construction_footprint_from_covered_fuel_area():
    module = load(Path(__file__).with_name("test_roof_joint.py"), "terminal_joint_case")
    bundle, transport, inventory, scenario, cells = module.joint_case.__wrapped__()
    engine = RoofJointEvaluationEngine(
        bundle, transport, inventory, source_policy="declared_fixed_sources"
    )
    plan = RoofJointPlan(
        "joint",
        ("M",),
        ground_evidence="synthetic",
        cleared_physical_cell_ids=(cells[1],),
    )
    verification = verify_terminal_fire(
        engine, replace(scenario, horizon_s=20), plan, horizon_cap_s=20
    )
    assert verification.summary["metrics"]["C_construction_action_area_m2"] == 50
    assert verification.record.intervention["cleared_vegetation_covered_area_m2"] == 10
    assert verification.fire_loss["physical"]["all"]["cleared_covered_area_m2"] == 35
    assert verification.fire_loss["ownership_reconstruction"]["retained_owner_subset_verified"]


def test_zero_clearance_shared_label_cannot_reduce_terminal_original_owner_N():
    from shapely.geometry import box

    engine, scenario = roof_case(
        radius=8,
        ignition_temperature_K=350,
        geometries={
            "S": box(4, 8, 6, 10),
            "M": box(6, 8, 8, 10),
            "U": box(6, 8, 8, 10),
            "G": box(8, 8, 10, 10),
            "F": box(8, 6, 10, 8),
        },
    )
    baseline = verify_terminal_fire(
        engine, scenario, BuildingDemolitionPlan("base"), horizon_cap_s=4000
    )
    changed = verify_terminal_fire(
        engine,
        scenario,
        BuildingDemolitionPlan(
            "alias", ("M",), ground_evidence="synthetic", overlap_policy="retain_shared_roof"
        ),
        horizon_cap_s=4000,
    )
    assert baseline.summary["terminal_complete"] and changed.summary["terminal_complete"]
    assert changed.summary["metrics"]["C_construction_action_area_m2"] == 0
    for name in (
        "N_ignited_annotation_attribution_count",
        "N_burned_out_annotation_attribution_count",
        "A_ignited_physical_covered_area_m2",
    ):
        assert baseline.summary["metrics"][name] == changed.summary["metrics"][name]
    assert (
        baseline.fire_loss["annotation_counts"]["retained_inventory_count"]
        != changed.fire_loss["annotation_counts"]["retained_inventory_count"]
    )


def test_joint_wrapper_preserves_each_inherited_source_family_without_new_draws():
    engine, original_scenario = roof_case(radius=0.1)
    plan = BuildingDemolitionPlan("base")
    scenarios = {f"family{i}-h1": (f"family{i}", 1.0) for i in range(4)}
    oracle = SimpleNamespace(
        plans={"base": plan},
        freeze_integration_membership=True,
        horizon_s=20,
        actual_source_for=lambda plan, sid: "S",
        scenarios=scenarios,
        source_policy="retained_sources",
        problem_sha256="frozen-four-family-problem",
        engine_for_scenario=lambda sid: engine,
        scenario_for=lambda sid, source: replace(original_scenario, scenario_id=sid),
        fires={
            label: {"footprints": {"S": SimpleNamespace(sha256="fixed-25m2")}}
            for label, _ in scenarios.values()
        },
        integration_mask_sha256="frozen-mask",
    )
    for scenario_id, (label, _) in scenarios.items():
        verification = evaluate_terminal_joint(oracle, "base", scenario_id, horizon_cap_s=20)
        evidence = verification.summary["model_protocol"]["source_evidence"]
        assert evidence["source_id"] == "S"
        assert evidence["fire_id"] == label
        assert evidence["source_footprint_sha256"] == "fixed-25m2"
        assert evidence["new_independent_fire_draw_count"] == 0
        assert verification.record.metrics["actual_source_ids"] == ["S"]
        assert (
            verification.summary["model_protocol"]["common_layout_sha256"] == engine.layout_sha256
        )
    oracle.actual_source_for = lambda plan, sid: None
    unknown = evaluate_terminal_joint(oracle, "base", next(iter(scenarios)), horizon_cap_s=20)
    assert unknown.check.status == "unknown"
    assert unknown.summary["new_CA_checks"] == 0
    assert unknown.result is None
    oracle.validate_plan = lambda plan: (_ for _ in ()).throw(ValueError("reference cap exceeded"))
    with pytest.raises(ValueError, match="reference cap"):
        evaluate_terminal_joint(oracle, "base", next(iter(scenarios)), horizon_cap_s=20)


@pytest.mark.parametrize("cap", [True, 0, -1, float("inf"), float("nan"), 19])
def test_invalid_or_shorter_time_caps_are_rejected(cap):
    engine, scenario = roof_case(radius=0.1)
    with pytest.raises(ValueError, match="terminal cap"):
        verify_terminal_fire(engine, scenario, BuildingDemolitionPlan("base"), horizon_cap_s=cap)


def test_summary_validator_rejects_tampered_certificate_and_unfinished_feasibility():
    engine, scenario = roof_case(radius=0.1)
    verification = verify_terminal_fire(
        engine, scenario, BuildingDemolitionPlan("base"), horizon_cap_s=1500
    )
    changed = deepcopy(verification.summary)
    changed["temporal_closure_certificate"]["actual_state_arrays_sha256"] = "0" * 64
    with pytest.raises(ValueError, match="fingerprint"):
        terminal_summary_is_complete(changed)
    changed = deepcopy(verification.summary)
    changed["terminal_complete"] = False
    with pytest.raises(ValueError, match="unfinished"):
        terminal_summary_is_complete(changed)


def test_saved_native_state_roundtrip_binds_the_terminal_certificate(tmp_path):
    engine, scenario = roof_case(radius=0.1)
    verified = verify_terminal_fire(
        engine, scenario, BuildingDemolitionPlan("base"), horizon_cap_s=1500
    )
    state = verified.result.final_state
    path = tmp_path / "final-state.npz"
    np.savez_compressed(path, **{key: getattr(state, key) for key in state.__dataclass_fields__})
    with np.load(path, allow_pickle=False) as saved:
        kwargs = {key: saved[key].copy() for key in V3State.__dataclass_fields__}
    kwargs["time_s"] = float(kwargs["time_s"])
    kwargs["step_index"] = int(kwargs["step_index"])
    loaded = V3State(**kwargs)
    peak = verified.record.diagnostics["maximum_valid_surface_temperature_by_node_K"]
    assert terminal_observation_is_complete(verified.summary, loaded, peak)
    loaded.fuel_remaining_kg[0, 0] += 1
    with pytest.raises(ValueError, match="loaded state"):
        terminal_observation_is_complete(verified.summary, loaded, peak)


def test_future_checkpoint_metrics_use_closure_without_claiming_simulated_temperatures():
    engine, scenario = roof_case(radius=0.1)
    verified = verify_terminal_fire(
        engine, scenario, BuildingDemolitionPlan("base"), horizon_cap_s=1500
    )
    summary = verified.summary
    assert 100 < summary["actual_stop_time_s"] < 1200
    curves = {
        "final_time_s": summary["actual_stop_time_s"],
        "checkpoints": [
            {"time_s": 100.0, "valid_observation": True},
            {"time_s": 1200.0, "valid_observation": False},
        ],
    }
    annotated = close_terminal_event_curves(curves, summary)
    before, after = annotated["checkpoints"]
    assert before["actual_time_reached"] and before["event_metrics_valid"]
    assert not before["event_metrics_closed_by_extinction_certificate"]
    assert not after["actual_time_reached"]
    assert not after["valid_observation"]
    assert after["event_metrics_closed_by_extinction_certificate"]
    assert after["future_temperature_fields_not_simulated"]
    assert after["event_metrics_valid"]
    assert annotated["terminal_endpoint"]["actual_stop_time_s"] == summary["actual_stop_time_s"]
    assert "actual_time_reached" not in curves["checkpoints"][1]
