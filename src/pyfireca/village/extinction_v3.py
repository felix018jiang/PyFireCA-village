"""Conservative time-closure certificates for the existing V3 thermal equations.

No fire physics or solver clock is changed. The caller must invoke the helper
at a completed solver boundary after scheduled events have been applied. With
no remaining sources or future events, the existing surface update is cooling
only in the verified domain. All claims concern this frozen model and event
list, and preserve the actual observation time.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict

import numpy as np

from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.physics_v2 import (
    advance_surface_heating_and_evaporation,
    net_surface_heat_flux_W_m2,
)
from pyfireca.village.solver_v3 import V3SimulationRequest, V3State
from pyfireca.village.state import VillageCellState


def _hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _array_hash(value):
    array = np.ascontiguousarray(value)
    result = hashlib.sha256()
    result.update(array.dtype.str.encode())
    result.update(str(array.shape).encode())
    result.update(array.tobytes())
    return result.hexdigest()


def extinction_certificate_sha256(certificate):
    """Fingerprint certificate contents using this schema's canonical encoding."""
    if not isinstance(certificate, dict):
        raise TypeError("extinction certificate mapping required")
    return _hash({key: value for key, value in certificate.items() if key != "certificate_sha256"})


def extinction_observation_fingerprints(state, maximum_valid_surface_temperature_K=None):
    """Bind native saved state and optional peak arrays without running a solver."""
    if not isinstance(state, V3State):
        raise TypeError("native V3 state required")
    arrays = {
        name: _array_hash(getattr(state, name))
        for name in state.__dataclass_fields__
        if isinstance(getattr(state, name), np.ndarray)
    }
    peak = (
        None
        if maximum_valid_surface_temperature_K is None
        else np.asarray(maximum_valid_surface_temperature_K, dtype=float).ravel()
    )
    return {
        "actual_state_arrays_sha256": _hash(arrays),
        "historical_valid_temperature_maximum_sha256": None if peak is None else _array_hash(peak),
    }


def certify_extinction_v3(
    request,
    state,
    *,
    post_apply_events_boundary=False,
    contact=None,
    maximum_valid_surface_temperature_K=None,
):
    """Return ``closed``, refusal reasons, and an optional bound certificate.

    There must be no BURNING state, no unprocessed/future direct ignition, no
    firebrand operator, and no currently eligible thermal ignition. Every
    susceptible temperature must be finite and at least the fixed ambient
    temperature. Existing equations with zero future source input must have
    finite nonpositive net flux and a nonincreasing one-step surface update.

    A supplied resolved contact operator must be the standard V3 operator. If
    request-level contact is enabled, its resolved operator is required. The
    old ``interval_mean_hrr_W`` is deliberately not treated as future power:
    at a burnout boundary it still describes the preceding nonzero interval.

    The proof assumes that the request, operators and static ignition permission
    remain fixed after certification. Fingerprints bind those inputs and the
    observed state; the certificate does not fabricate elapsed simulation time.
    """
    if not isinstance(request, V3SimulationRequest) or not isinstance(state, V3State):
        raise TypeError("V3 request and solver state required")
    if not isinstance(post_apply_events_boundary, bool):
        raise TypeError("post_apply_events_boundary must be an explicit boolean")
    reasons = []
    if not post_apply_events_boundary:
        reasons.append("not_a_confirmed_post_apply_events_step_boundary")
    if request.firebrands is not None:
        reasons.append("firebrands_or_delayed_ember_ignitions_not_supported")
    if state.state.shape != request.grid.shape:
        return {
            "closed": False,
            "rejection_reasons": [*reasons, "state_layout_mismatch"],
            "certificate": None,
        }
    if np.any(state.state == int(VillageCellState.BURNING)):
        reasons.append("active_burning_nodes_remain")
    if (
        not np.isfinite(state.time_s)
        or not 0 <= state.time_s <= request.scenario.end_time_s + 1e-10
    ):
        reasons.append("invalid_actual_observation_time")
    if any(event.time_s > state.time_s + 1e-10 for event in request.scenario.ignitions):
        reasons.append("future_prescribed_ignitions_remain")
    if contact is not None and not isinstance(contact, FlameContactOperator):
        reasons.append("unsupported_contact_operator")
    if contact is None and request.flame_contact is not None:
        reasons.append("resolved_contact_operator_required")
    if reasons:
        return {"closed": False, "rejection_reasons": reasons, "certificate": None}

    shape = request.grid.shape
    fields = state.__dataclass_fields__
    if any(
        isinstance(getattr(state, name), np.ndarray) and getattr(state, name).shape != shape
        for name in fields
    ):
        return {
            "closed": False,
            "rejection_reasons": ["state_array_layout_mismatch"],
            "certificate": None,
        }
    susceptible = np.isin(
        state.state, [int(VillageCellState.UNBURNED), int(VillageCellState.HEATING)]
    )
    burned_out = state.state == int(VillageCellState.BURNED_OUT)
    valid = state.temperature_valid_mask
    if valid.dtype != np.bool_ or not np.array_equal(valid, susceptible):
        reasons.append("temperature_validity_does_not_match_susceptible_state")
    if any(
        state.state[event.row, event.col] != int(VillageCellState.BURNED_OUT)
        for event in request.scenario.ignitions
    ):
        reasons.append("due_direct_ignition_not_applied_or_not_finished")
    ignition, burnout = state.ignition_time_s, state.burnout_time_s
    if (
        not np.array_equal(np.isfinite(ignition), burned_out)
        or not np.array_equal(np.isfinite(burnout), burned_out)
        or np.any(ignition[burned_out] < 0)
        or np.any(burnout[burned_out] < ignition[burned_out])
        or np.any(burnout[burned_out] > state.time_s + 1e-10)
    ):
        reasons.append("event_clocks_do_not_match_completed_fire_states")
    parameters = request.parameters
    if any(
        not np.isfinite(getattr(state, name)).all() or np.any(getattr(state, name) < 0)
        for name in (
            "interval_mean_hrr_W",
            "incident_radiation_J",
            "absorbed_radiation_J",
            "received_convection_J",
            "released_energy_J",
        )
    ):
        reasons.append("invalid_saved_heat_or_interval_power_fields")
    if any(
        not np.isfinite(getattr(parameters, name)[susceptible]).all()
        or np.any(getattr(parameters, name)[susceptible] < 0)
        for name in (
            "emissivity",
            "ambient_heat_transfer_coefficient_W_m2_K",
            "backing_conductance_W_m2_K",
        )
    ):
        reasons.append("invalid_nonnegative_surface_loss_coefficients")
    initial_fuel = np.where(request.burnable_mask, parameters.pyrolysable_dry_fuel_mass_kg, 0)
    fuel_tolerance = np.maximum(1e-12, initial_fuel * 1e-9)
    if (
        not np.isfinite(state.fuel_remaining_kg).all()
        or np.any(state.fuel_remaining_kg < 0)
        or np.any(state.fuel_remaining_kg > initial_fuel + fuel_tolerance)
        or np.any(state.fuel_remaining_kg[burned_out] != 0)
    ):
        reasons.append("invalid_remaining_fuel")
    water = state.heated_water_remaining_kg
    initial_water = np.where(
        request.burnable_mask,
        parameters.effective_heated_dry_mass_kg * parameters.moisture_content_dry_basis,
        0,
    )
    if not np.isfinite(water).all() or np.any(water < 0) or np.any(water > initial_water + 1e-9):
        reasons.append("invalid_remaining_heated_water")
    temperature = state.surface_temperature_K
    ambient = request.scenario.ambient_temperature_K
    if not np.isfinite(temperature[valid]).all() or np.any(temperature[valid] < ambient):
        reasons.append("susceptible_temperature_outside_cooling_domain")
    eligible = (
        susceptible
        & request.burnable_mask
        & request.piloted_ignition_mask
        & (state.fuel_remaining_kg > fuel_tolerance)
    )
    ready = eligible & (temperature >= parameters.ignition_temperature_K)
    if ready.any():
        reasons.append("thermal_ignition_already_eligible_at_current_temperature")
    if reasons:
        return {"closed": False, "rejection_reasons": reasons, "certificate": None}

    zero = np.zeros(shape)
    try:
        transfer = None if contact is None else contact.transfer(zero, temperature, susceptible)
        convection = zero if transfer is None else transfer.target_power_W
        fractions = zero if transfer is None else transfer.target_contact_fraction
        if np.any(convection != 0) or np.any(fractions != 0):
            reasons.append("resolved_contact_has_nonzero_zero_source_transfer")
        areas = parameters.covered_area_m2[susceptible]
        flux = net_surface_heat_flux_W_m2(
            temperature[susceptible],
            zero[susceptible],
            convection[susceptible] / areas,
            parameters.absorptivity[susceptible],
            parameters.emissivity[susceptible],
            parameters.ambient_heat_transfer_coefficient_W_m2_K[susceptible]
            * (1 - fractions[susceptible]),
            parameters.backing_conductance_W_m2_K[susceptible],
            ambient,
        )
        if not np.isfinite(flux).all() or np.any(flux > 0):
            reasons.append("zero_source_net_heat_not_finite_nonpositive")
        ambient_flux = net_surface_heat_flux_W_m2(
            np.full(areas.shape, ambient),
            zero[susceptible],
            zero[susceptible],
            parameters.absorptivity[susceptible],
            parameters.emissivity[susceptible],
            parameters.ambient_heat_transfer_coefficient_W_m2_K[susceptible],
            parameters.backing_conductance_W_m2_K[susceptible],
            ambient,
        )
        if not np.isfinite(ambient_flux).all() or np.any(ambient_flux > 0):
            reasons.append("ambient_zero_source_net_heat_not_nonpositive")
        next_t, next_w = advance_surface_heating_and_evaporation(
            temperature[susceptible],
            water[susceptible] / areas,
            parameters.effective_heated_dry_mass_kg[susceptible] / areas,
            parameters.specific_heat_J_kg_K[susceptible],
            flux,
            request.scenario.max_dt_s,
            ambient_temperature_K=ambient,
        )
        if (
            not np.isfinite(next_t).all()
            or np.any(next_t > temperature[susceptible])
            or np.any(next_t < ambient)
            or not np.array_equal(next_w, water[susceptible] / areas)
            or not np.array_equal(next_w * areas, water[susceptible])
        ):
            reasons.append("existing_zero_source_surface_update_not_cooling_only")
    except (ValueError, FloatingPointError, OverflowError):
        return {
            "closed": False,
            "rejection_reasons": [*reasons, "zero_source_surface_equations_invalid"],
            "certificate": None,
        }
    if reasons:
        return {"closed": False, "rejection_reasons": reasons, "certificate": None}

    peak = None
    if maximum_valid_surface_temperature_K is not None:
        peak = np.asarray(maximum_valid_surface_temperature_K, dtype=float).ravel()
        if (
            peak.size != temperature.size
            or not np.isfinite(peak).all()
            or np.any(peak[valid.ravel()] < temperature[valid])
        ):
            return {
                "closed": False,
                "rejection_reasons": ["historical_temperature_maximum_inconsistent"],
                "certificate": None,
            }

    input_hashes = {
        "parameters": {
            name: _array_hash(getattr(parameters, name)) for name in parameters.__dataclass_fields__
        },
        "masks": {
            name: _array_hash(getattr(request, name))
            for name in (
                "domain_mask",
                "known_mask",
                "burnable_mask",
                "source_mask",
                "target_mask",
                "piloted_ignition_mask",
            )
        },
        "radiation_edges": {
            name: _array_hash(getattr(request.exposure_edges, name))
            for name in ("source", "target", "weight")
        },
        "scenario": {
            "max_dt_s": float(request.scenario.max_dt_s),
            "end_time_s": float(request.scenario.end_time_s),
            "ambient_temperature_K": float(ambient),
            "heating_temperature_delta_K": float(request.scenario.heating_temperature_delta_K),
            "ignitions": [
                {"time_s": float(e.time_s), "row": int(e.row), "col": int(e.col), "kind": e.kind}
                for e in request.scenario.ignitions
            ],
        },
        "grid": asdict(request.grid),
        "contact": None
        if contact is None
        else {
            "parameters": asdict(contact.parameters),
            "neighbors": _array_hash(contact.neighbors),
            "geometry_fraction": _array_hash(contact.geometry_fraction),
        },
    }
    certificate = {
        "schema_version": "v3-extinction-time-closure-1",
        "actual_stop_time_s": float(state.time_s),
        "requested_horizon_s": float(request.scenario.end_time_s),
        "actual_step_index": int(state.step_index),
        "last_burnout_event_time_s": float(burnout[burned_out].max()) if burned_out.any() else None,
        "post_apply_events_boundary": True,
        "future_prescribed_event_count": 0,
        "firebrands": None,
        "burning_node_count": 0,
        "susceptible_node_count": int(susceptible.sum()),
        "eligible_thermal_ignition_node_count": int(ready.sum()),
        "minimum_eligible_ignition_slack_K": float(
            np.min(parameters.ignition_temperature_K[eligible] - temperature[eligible])
        )
        if eligible.any()
        else None,
        "minimum_susceptible_temperature_above_ambient_K": float(
            np.min(temperature[susceptible] - ambient)
        )
        if susceptible.any()
        else None,
        "maximum_zero_source_net_flux_W_m2": float(flux.max()) if len(flux) else None,
        "maximum_ambient_zero_source_net_flux_W_m2": float(ambient_flux.max())
        if len(ambient_flux)
        else None,
        "heated_water_storage_roundtrip_stationary": True,
        "maximum_last_interval_hrr_W": float(state.interval_mean_hrr_W.max()),
        "last_interval_hrr_is_not_future_source_power": True,
        "future_mean_hrr_W": 0.0,
        "fuel_remaining_kg": float(state.fuel_remaining_kg.sum()),
        "heated_water_remaining_kg": float(water.sum()),
        "ignited_node_count": int(np.isfinite(ignition).sum()),
        "burned_out_node_count": int(burned_out.sum()),
        "cumulative_ignited_covered_area_m2": float(
            parameters.covered_area_m2[np.isfinite(ignition)].sum()
        ),
        "cumulative_burned_out_covered_area_m2": float(
            parameters.covered_area_m2[burned_out].sum()
        ),
        "future_invariants": [
            "fuel_and_heated_water_unchanged",
            "no_new_ignition_or_burnout_events",
            "no_additional_radiative_contact_or_released_energy",
            "susceptible_temperature_nonincreasing",
            "historical_valid_temperature_maxima_unchanged",
        ],
        "frozen_input_sha256": _hash(input_hashes),
        **extinction_observation_fingerprints(state, peak),
        "scope": (
            "same_frozen_V3_inputs_static_permissions_and_declared_event_list_after_actual_stop"
        ),
        "reached_requested_horizon": bool(state.time_s >= request.scenario.end_time_s - 1e-8),
        "future_temperature_fields_not_simulated": True,
    }
    certificate["certificate_sha256"] = extinction_certificate_sha256(certificate)
    return {"closed": True, "rejection_reasons": [], "certificate": certificate}
