"""Whole-model-fire verification on a previously declared physical protocol.

The time cap is a computing limit, not a completed-fire claim. Only a freshly
checked extinction certificate closes terminal losses. Changing the requested
horizon can change a final clipped solver step; this is a new verification
protocol and never transfers old objective values or optimality statements.
"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, replace
from math import isfinite

import numpy as np

from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.engines.roof_heritage import RoofHeritageEvaluationEngine
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.fire_loss import fire_loss_from_result
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import BuildingDemolitionPlan
from pyfireca.village.extinction_v3 import (
    certify_extinction_v3,
    extinction_certificate_sha256,
    extinction_observation_fingerprints,
)


@dataclass(frozen=True)
class TerminalFireVerification:
    """Native result and separate terminal completeness, protection and losses."""

    check: ProtectionCheck
    summary: dict
    record: object = None
    layout: object = None
    result: object = None
    fire_loss: dict | None = None


def terminal_summary_is_complete(summary):
    """Validate a terminal-summary record and return its completeness flag.

    This checks schema, internal bindings and certificate fingerprints. Readers
    must also verify their frozen artifact manifest. It does not replace fresh
    certification against native request/state arrays by ``audit_terminal_result``.
    Unknown, cap-limited and physically invalid results return False; malformed
    or inconsistent claims raise ValueError and cannot enter a final frontier.
    """
    if (
        not isinstance(summary, dict)
        or summary.get("schema_version") != "heritage-terminal-fire-verification-1"
        or not isinstance(summary.get("terminal_complete"), bool)
    ):
        raise ValueError("terminal verification summary schema required")
    if not summary["terminal_complete"]:
        if (
            summary.get("terminal_status") != "unknown"
            or summary.get("protection_feasible") is not False
            or summary.get("fire_loss_complete") is not False
        ):
            raise ValueError(
                "unfinished terminal observation cannot claim final losses or feasibility"
            )
        return False
    certificate = summary.get("temporal_closure_certificate")
    protocol = summary.get("model_protocol")
    if not isinstance(certificate, dict) or not isinstance(protocol, dict):
        raise ValueError(
            "complete terminal verification requires its closure certificate and protocol"
        )
    if (
        certificate.get("schema_version") != "v3-extinction-time-closure-1"
        or certificate.get("certificate_sha256") != extinction_certificate_sha256(certificate)
        or summary.get("terminal_protocol_sha256") != content_hash(protocol)
    ):
        raise ValueError("terminal certificate or protocol fingerprint differs")
    for key in (
        "frozen_input_sha256",
        "actual_state_arrays_sha256",
        "historical_valid_temperature_maximum_sha256",
    ):
        digest = certificate.get(key)
        if (
            not isinstance(digest, str)
            or len(digest) != 64
            or any(c not in "0123456789abcdef" for c in digest)
        ):
            raise ValueError("terminal certificate must bind actual input, state and peak arrays")
    actual, cap = certificate.get("actual_stop_time_s"), protocol.get("terminal_horizon_cap_s")
    if (
        isinstance(actual, bool)
        or not isinstance(actual, (int, float))
        or not isfinite(actual)
        or actual < 0
    ):
        raise ValueError("invalid actual terminal observation time")
    _cap(cap, 0)
    if actual > cap + 1e-10:
        raise ValueError("actual terminal time exceeds its declared cap")
    reached = actual >= cap - 1e-8
    if (
        actual != summary.get("actual_stop_time_s")
        or certificate.get("requested_horizon_s") != cap
        or certificate.get("reached_requested_horizon") != reached
        or summary.get("reached_requested_horizon") != reached
        or certificate.get("post_apply_events_boundary") is not True
        or certificate.get("future_prescribed_event_count") != 0
        or certificate.get("firebrands") is not None
        or certificate.get("burning_node_count") != 0
        or certificate.get("eligible_thermal_ignition_node_count") != 0
        or certificate.get("heated_water_storage_roundtrip_stationary") is not True
        or certificate.get("future_mean_hrr_W") != 0
        or summary.get("fresh_certificate_checked") is not True
        or summary.get("invalid_reasons") != []
        or summary.get("closure_rejection_reasons") != []
    ):
        raise ValueError("terminal completeness differs from actual extinction conditions")
    for key, positive in (
        ("minimum_eligible_ignition_slack_K", True),
        ("minimum_susceptible_temperature_above_ambient_K", False),
    ):
        value = certificate.get(key)
        if value is not None and (
            not isinstance(value, (int, float))
            or not isfinite(value)
            or (value <= 0 if positive else value < 0)
        ):
            raise ValueError("terminal certificate is outside its cooling and ignition domain")
    for key in (
        "maximum_zero_source_net_flux_W_m2",
        "maximum_ambient_zero_source_net_flux_W_m2",
    ):
        value = certificate.get(key)
        if value is not None and (
            not isinstance(value, (int, float)) or not isfinite(value) or value > 0
        ):
            raise ValueError(
                "terminal certificate contains positive or nonfinite future heat input"
            )
    status = summary.get("terminal_status")
    failed = bool(summary.get("ignited_core_ids"))
    if (
        status != ("failed" if failed else "passed")
        or summary.get("core_ignition_observed") != failed
        or summary.get("core_safety_through_closure") != status
        or summary.get("protection_feasible") != (status == "passed")
        or summary.get("fire_loss_complete") is not True
    ):
        raise ValueError("terminal core safety differs from its closed result")
    return True


def terminal_observation_is_complete(summary, state, maximum_valid_surface_temperature_K):
    """Also bind a validated summary to loaded native state and peak arrays.

    Frozen artifact manifests and the declared physical problem must still be
    verified by the reader. Full equation-based certification is performed by
    ``audit_terminal_result`` when the resolved request/operators are available.
    """
    if not terminal_summary_is_complete(summary):
        return False
    certificate = summary["temporal_closure_certificate"]
    fingerprints = extinction_observation_fingerprints(state, maximum_valid_surface_temperature_K)
    if (
        any(certificate[key] != value for key, value in fingerprints.items())
        or state.time_s != summary["actual_stop_time_s"]
        or state.step_index != certificate["actual_step_index"]
    ):
        raise ValueError("terminal certificate differs from loaded state or peak arrays")
    return True


def close_terminal_event_curves(curves, summary):
    """Annotate event-derived checkpoints while preserving actual observations.

    A checkpoint beyond actual stop time is valid only through strict time
    closure. Its event metrics may then be inferred; future temperature fields
    were never simulated. Existing ``valid_observation`` flags remain intact.
    """
    complete = terminal_summary_is_complete(summary)
    actual = summary["actual_stop_time_s"]
    cap = summary["model_protocol"]["terminal_horizon_cap_s"]
    if curves.get("final_time_s") != actual:
        raise ValueError("terminal curves differ from actual saved time")
    annotated = deepcopy(curves)
    for row in annotated["checkpoints"]:
        checkpoint = row["time_s"]
        if not isinstance(checkpoint, (int, float)) or not 0 <= checkpoint <= cap:
            raise ValueError("terminal event checkpoints must lie within the declared cap")
        future = checkpoint > actual
        closed = complete and future
        row.update(
            actual_time_reached=not future,
            event_metrics_closed_by_extinction_certificate=closed,
            event_metrics_valid=bool(row["valid_observation"] or closed),
            future_temperature_fields_not_simulated=future,
            event_metric_basis="strict_extinction_time_closure"
            if closed
            else "simulated_event_history"
            if row["valid_observation"]
            else "unclosed_future_or_invalid_observation",
        )
    annotated["terminal_endpoint"] = {
        key: deepcopy(summary[key])
        for key in (
            "actual_stop_time_s",
            "terminal_status",
            "terminal_complete",
            "core_ignition_observed",
            "ignited_core_ids",
            "fire_loss_complete",
            "metrics",
        )
    }
    annotated["evaluation_temporal_protocol"] = deepcopy(summary["model_protocol"])
    return annotated


def _cap(value, reference):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(value)
        or value <= 0
        or value < reference
    ):
        raise ValueError("terminal cap must be finite positive and at least the reference horizon")
    return float(value)


def _loss_metrics(record, loss):
    physical = loss["physical"]
    counts = loss["physical_remaining_roof_annotation_counts"]
    construction = record.intervention.get(
        "cleared_action_geometry_union_area_m2",
        record.intervention["cleared_roof_union_area_m2"],
    )
    return {
        "C_construction_action_area_m2": float(construction),
        "A_ignited_physical_covered_area_m2": physical["all"]["cumulative_ignited_covered_area_m2"],
        "ignited_roof_covered_area_m2": physical["roof"]["cumulative_ignited_covered_area_m2"],
        "ignited_nonroof_covered_area_m2": physical["nonroof"][
            "cumulative_ignited_covered_area_m2"
        ],
        "burned_out_physical_covered_area_m2": physical["all"][
            "cumulative_burned_out_covered_area_m2"
        ],
        "clearing_plus_ignited_physical_covered_area_m2": physical["all"][
            "clearing_plus_ignited_union_area_m2"
        ],
        "N_ignited_annotation_attribution_count": counts["ignited_annotation_attribution_count"],
        "N_burned_out_annotation_attribution_count": counts[
            "burned_out_annotation_attribution_count"
        ],
        "administrative_clearing_plus_ignited_annotation_count": loss["annotation_counts"][
            "clearing_plus_ignited_annotation_count"
        ],
        "fire_depleted_pyrolysable_fuel_kg": loss["fuel"]["fire_depleted_kg"],
        "clearing_plus_fire_depleted_pyrolysable_fuel_kg": loss["fuel"][
            "clearing_plus_fire_depleted_kg"
        ],
        "real_destroyed_building_count": None,
        "structural_damage_area_m2": None,
    }


def audit_terminal_result(
    engine,
    scenario,
    plan,
    record,
    layout,
    result,
    *,
    contact,
    reference_horizon_s=None,
    reference_problem_sha256=None,
    source_evidence=None,
):
    """Certify a saved native result without rerunning CA or extending its time.

    ``contact`` must be the resolved operator for this request. An existing
    engine certificate, if present, must exactly match fresh certification.
    Without one, a freshly checked certificate can close an archived actual
    boundary, but cannot invent an earlier extinction observation.
    """
    if not isinstance(engine, RoofHeritageEvaluationEngine) or not isinstance(
        scenario, EvaluationScenario
    ):
        raise TypeError("typed roof engine and scenario required")
    if not isinstance(plan, BuildingDemolitionPlan):
        raise TypeError("typed building or joint intervention required")
    request, state = layout.node_request, result.final_state
    cap = _cap(scenario.horizon_s, 0)
    reference_horizon = cap if reference_horizon_s is None else float(reference_horizon_s)
    _cap(cap, reference_horizon)
    if not isfinite(reference_horizon) or reference_horizon <= 0:
        raise ValueError("positive finite reference horizon required")
    if (
        request.scenario.end_time_s != cap
        or record.horizon_s != cap
        or record.scenario_id != scenario.scenario_id
        or record.intervention_id != plan.plan_id
        or record.case_sha256 != engine.case_sha256
        or record.diagnostics["simulated_until_s"] != state.time_s
        or record.diagnostics["physical_request_sha256"] != request_fingerprint(request)
        or not np.array_equal(layout.cell_id, engine.layout.cell_id)
        or layout.component_id != engine.layout.component_id
    ):
        raise ValueError("terminal native result differs from its declared request or identity")
    actual_reached = bool(state.time_s >= cap - 1e-8)
    if record.diagnostics["reached_requested_horizon"] != actual_reached:
        raise ValueError("reached-horizon flag must describe actual elapsed solver time")
    peak = record.diagnostics["maximum_valid_surface_temperature_by_node_K"]
    attempt = certify_extinction_v3(
        request,
        state,
        contact=contact,
        post_apply_events_boundary=True,
        maximum_valid_surface_temperature_K=peak,
    )
    certificate = attempt["certificate"]
    saved_certificate = record.diagnostics.get("temporal_closure_certificate")
    if saved_certificate is not None and saved_certificate != certificate:
        raise ValueError("saved extinction certificate differs from the actual request and state")
    observed_core_ids = sorted(
        identifier
        for identifier, nodes in engine.core_nodes.items()
        if np.isfinite(state.ignition_time_s.ravel()[nodes]).any()
    )
    if observed_core_ids != sorted(record.metrics["ignited_core_ids"]):
        raise ValueError("recorded core safety differs from actual ignition events")
    sources = tuple(record.metrics["actual_source_ids"])
    if source_evidence is not None and sources != (source_evidence.get("source_id"),):
        raise ValueError("actual fire source differs from the frozen realization")
    if source_evidence is not None:
        source_evidence = dict(source_evidence)
        direct = [event.col for event in request.scenario.ignitions]
        source_evidence["source_budget"] = {
            name: float(getattr(request.parameters, name).ravel()[direct].sum())
            for name in ("covered_area_m2", "initial_dry_fuel_mass_kg", "available_energy_J")
        }
    invalid = list(record.diagnostics["invalid_reasons"])
    if not record.diagnostics["source_verified"]:
        invalid.append("source_not_verified")
    complete = bool(attempt["closed"] and not invalid)
    status = "unknown" if not complete else "failed" if observed_core_ids else "passed"
    loss = fire_loss_from_result(
        layout,
        result,
        engine.inventory,
        demolished_ids=plan.demolished_ids,
        source_ids=sources,
        original_node_owners=engine.layout.owners,
    )
    protocol = {
        "reference_problem_sha256": reference_problem_sha256,
        "reference_horizon_s": reference_horizon,
        "terminal_horizon_cap_s": cap,
        "horizon_was_extended": cap != reference_horizon,
        "horizon_change_can_change_final_clipped_timestep": True,
        "previous_objective_values_or_optimality_transferred": False,
        "stop_at_first_core_ignition": record.diagnostics["stop_at_first_core_ignition"],
        "stop_at_extinction": record.diagnostics.get("stop_at_extinction", False),
        "closure_audited_at_actual_saved_boundary": True,
        "max_dt_s": float(request.scenario.max_dt_s),
        "parent_case_sha256": engine.case_sha256,
        "common_layout_sha256": engine.layout_sha256,
        "physical_request_sha256": request_fingerprint(request),
        "source_evidence": source_evidence,
        "scope": "frozen_model_and_declared_sources;whole_fire_losses_only_when_closed",
    }
    summary = {
        "schema_version": "heritage-terminal-fire-verification-1",
        "plan_id": plan.plan_id,
        "scenario_id": scenario.scenario_id,
        "demolished_ids": list(plan.demolished_ids),
        "cleared_physical_cell_ids": list(getattr(plan, "cleared_physical_cell_ids", ())),
        "terminal_status": status,
        "terminal_complete": complete,
        "terminal_observation": "invalid_physical_domain"
        if invalid
        else "strict_extinction_closed"
        if attempt["closed"]
        else "time_cap_reached_without_extinction_closure"
        if actual_reached
        else "partial_without_extinction_closure",
        "core_safety_through_closure": status,
        "core_ignition_observed": bool(observed_core_ids),
        "ignited_core_ids": observed_core_ids,
        "protection_feasible": status == "passed",
        "fire_loss_complete": complete,
        "actual_stop_time_s": float(state.time_s),
        "new_CA_checks": 0,
        "reached_requested_horizon": actual_reached,
        "temporal_closure_certificate": certificate,
        "fresh_certificate_checked": True,
        "engine_certificate_was_present": saved_certificate is not None,
        "closure_rejection_reasons": attempt["rejection_reasons"],
        "invalid_reasons": invalid,
        "metrics": _loss_metrics(record, loss),
        "metric_scope": (
            "C=roof_geometry_union_plus_whole_ground_action_footprints;"
            "A=original_disjoint_physical_covered_nodes;"
            "N=original_owner_attribution_on_actual_remaining_roofs;"
            "annotation_bounds_not_real_destroyed_buildings"
        ),
        "model_protocol": protocol,
        "terminal_protocol_sha256": content_hash(protocol),
        "global_optimality_proven": False,
        "pareto_frontier_complete": False,
    }
    terminal_summary_is_complete(summary)
    return TerminalFireVerification(
        ProtectionCheck(status, summary), summary, record, layout, result, loss
    )


def verify_terminal_fire(
    engine,
    scenario,
    plan,
    *,
    horizon_cap_s=21600.0,
    reference_problem_sha256=None,
    source_evidence=None,
):
    """Simulate one declared plan/fire until strict closure or the explicit cap.

    Core failures continue burning so their complete model losses can be
    compared. A cap-limited unfinished fire is terminal-unknown even when the
    original engine successfully evaluated protection over that finite cap.
    """
    if not isinstance(scenario, EvaluationScenario):
        raise TypeError("typed evaluation scenario required")
    if not isinstance(engine, RoofHeritageEvaluationEngine) or not isinstance(
        plan, BuildingDemolitionPlan
    ):
        raise TypeError("typed roof engine and intervention required")
    cap = _cap(horizon_cap_s, scenario.horizon_s)
    terminal_scenario = replace(scenario, horizon_s=cap)
    record, layout, result = engine.simulate(
        terminal_scenario,
        plan,
        stop_at_first_core_ignition=False,
        stop_at_extinction=True,
    )
    verification = audit_terminal_result(
        engine,
        terminal_scenario,
        plan,
        record,
        layout,
        result,
        contact=engine.last_contact,
        reference_horizon_s=scenario.horizon_s,
        reference_problem_sha256=reference_problem_sha256,
        source_evidence=source_evidence,
    )
    verification.summary["new_CA_checks"] = 1
    return verification


def evaluate_terminal_joint(oracle, plan, scenario_id, *, horizon_cap_s=21600.0):
    """Use a joint oracle's frozen mesh, source prefix, q and dt unchanged.

    No source families or draws are added. Calling this for every originally
    declared scenario preserves all inherited fire families and scale checks.
    """
    if isinstance(plan, str):
        plan = oracle.plans[plan]
    if plan.plan_id not in oracle.plans or plan != oracle.plans[plan.plan_id]:
        raise ValueError("terminal plan differs from the frozen joint declaration")
    if hasattr(oracle, "validate_plan"):
        oracle.validate_plan(plan)
    if not oracle.freeze_integration_membership:
        raise ValueError("terminal joint comparison requires frozen integration membership")
    _cap(horizon_cap_s, oracle.horizon_s)
    source_id = oracle.actual_source_for(plan, scenario_id)
    label, scale = oracle.scenarios[scenario_id]
    reference_problem = getattr(oracle, "reference_problem_sha256", oracle.problem_sha256)
    evidence = {
        "source_id": source_id,
        "fire_id": label,
        "scale_m": scale,
        "source_policy": oracle.source_policy,
        "quadrature_spacing_m": getattr(oracle, "quadrature_spacing_m", None),
        "source_area_limit_m2": getattr(oracle, "source_area_limit_m2", None),
        "source_area_tolerance_m2": getattr(oracle, "source_area_tolerance_m2", None),
        "new_independent_fire_draw_count": 0,
        "reference_problem_sha256": reference_problem,
        "comparison_problem_sha256": oracle.problem_sha256,
    }
    if source_id is None:
        summary = {
            "schema_version": "heritage-terminal-fire-verification-1",
            "plan_id": plan.plan_id,
            "scenario_id": scenario_id,
            "terminal_status": "unknown",
            "terminal_complete": False,
            "protection_feasible": False,
            "fire_loss_complete": False,
            "reason": "no_retained_source_in_frozen_prefix",
            "source_evidence": evidence,
            "actual_stop_time_s": None,
            "new_CA_checks": 0,
            "global_optimality_proven": False,
        }
        return TerminalFireVerification(ProtectionCheck("unknown", summary), summary)
    engine = oracle.engine_for_scenario(scenario_id)
    evidence.update(
        source_footprint_sha256=oracle.fires[label]["footprints"][source_id].sha256,
        frozen_integrated_node_mask_sha256=oracle.integration_mask_sha256,
    )
    return verify_terminal_fire(
        engine,
        oracle.scenario_for(scenario_id, source_id),
        plan,
        horizon_cap_s=horizon_cap_s,
        reference_problem_sha256=reference_problem,
        source_evidence=evidence,
    )
