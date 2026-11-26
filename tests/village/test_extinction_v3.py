"""Time closure uses the existing V3 equations and preserves actual stop time."""

import importlib.util
import json
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from pyfireca.village.extinction_v3 import certify_extinction_v3
from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.state import VillageCellState

ROOT = Path(__file__).resolve().parents[2]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def small():
    return load("extinction_small", Path(__file__).with_name("test_solver_v3.py"))


def contact_for(request):
    if request.flame_contact is None:
        return None
    return FlameContactOperator.build(
        request.grid,
        request.burnable_mask,
        request.parameters,
        request.flame_contact,
        request.ground_elevation_m,
        request.material_index,
    )


def run_observed(request, *, stop):
    peak = np.full(request.grid.shape, request.scenario.ambient_temperature_K, dtype=float)
    operator = contact_for(request)
    closure = None

    def observe(state):
        nonlocal closure
        np.maximum(
            peak, np.where(state.temperature_valid_mask, state.surface_temperature_K, 0), out=peak
        )
        if closure is None:
            attempt = certify_extinction_v3(
                request,
                state,
                post_apply_events_boundary=True,
                contact=operator,
                maximum_valid_surface_temperature_K=peak,
            )
            if attempt["closed"]:
                closure = attempt["certificate"]
        return stop and closure is not None

    result = run_v3_simulation(request, stop_when=observe, retain_snapshots=False)
    return result, peak, closure


@pytest.mark.parametrize(
    "name",
    [
        "grass_chain",
        "inert_gap",
        "heat_without_ignition",
        "shrub_chain",
        "pilot_disabled",
    ],
)
def test_full_horizon_matches_closed_prefix_for_events_fuel_water_energy_and_peak(name, small):
    if name == "pilot_disabled":
        request = small._request(
            ("grass", "grass"), edge_weight=0.08, heat_loss_W_m2_K=1, end_time_s=30
        )
        pilot = request.piloted_ignition_mask.copy()
        pilot[0, 1] = False
        request = replace(request, piloted_ignition_mask=pilot)
    else:
        core = load("extinction_core", ROOT / "examples/village/vegetation_v3/audit_core.py")
        request = core.make_core_request(name)
    complete, full_peak, _ = run_observed(request, stop=False)
    prefix, peak, certificate = run_observed(request, stop=True)
    assert certificate is not None
    assert prefix.final_state.time_s < complete.final_state.time_s
    assert certificate["actual_stop_time_s"] == prefix.final_state.time_s
    assert not certificate["reached_requested_horizon"]
    assert certificate["heated_water_storage_roundtrip_stationary"]
    assert certificate["future_mean_hrr_W"] == 0
    assert prefix.final_state.interval_mean_hrr_W.max() > 0  # Previous interval, not future power.
    for field in (
        "fuel_remaining_kg",
        "ignition_time_s",
        "burnout_time_s",
        "burn_age_s",
        "heated_water_remaining_kg",
        "incident_radiation_J",
        "absorbed_radiation_J",
        "received_convection_J",
        "released_energy_J",
    ):
        np.testing.assert_array_equal(
            getattr(prefix.final_state, field), getattr(complete.final_state, field)
        )
    np.testing.assert_array_equal(peak, full_peak)
    json.dumps(certificate, allow_nan=False)
    if name == "pilot_disabled":
        assert peak[0, 1] > request.parameters.ignition_temperature_K[0, 1]
        assert not np.isfinite(prefix.final_state.ignition_time_s[0, 1])


def no_burning_prefix(request):
    return run_v3_simulation(
        request,
        retain_snapshots=False,
        stop_when=lambda state: (
            state.time_s > 0 and not np.any(state.state == int(VillageCellState.BURNING))
        ),
    )


def test_future_direct_fire_prevents_closure_during_a_burning_gap(small):
    request = small._request(
        ("grass", "grass"),
        end_time_s=12,
        events=(
            VillageIgnitionEvent(0, 0, 0),
            VillageIgnitionEvent(5, 0, 1),
        ),
    )
    prefix = no_burning_prefix(request)
    attempt = certify_extinction_v3(request, prefix.final_state, post_apply_events_boundary=True)
    assert not attempt["closed"]
    assert "future_prescribed_ignitions_remain" in attempt["rejection_reasons"]
    full = run_v3_simulation(request, retain_snapshots=False)
    assert full.final_state.ignition_time_s[0, 1] == 5


def test_warm_ambient_wet_fuel_can_reignite_after_a_no_burning_gap(small):
    request = small._request(
        ("grass", "grass"), edge_weight=0.08, heat_loss_W_m2_K=20, end_time_s=400
    )
    parameters = request.parameters
    water = parameters.water_mass_kg.copy()
    water[0, 1] = 2
    moisture = parameters.moisture_content_dry_basis.copy()
    moisture[0, 1] = 2
    threshold = parameters.ignition_temperature_K.copy()
    threshold[0, 1] = 390
    request = replace(
        request,
        parameters=replace(
            parameters,
            water_mass_kg=water,
            dead_water_mass_kg=water,
            moisture_content_dry_basis=moisture,
            ignition_temperature_K=threshold,
        ),
        scenario=replace(request.scenario, ambient_temperature_K=400.0),
    )
    prefix = no_burning_prefix(request)
    assert prefix.final_state.time_s == 2.25
    attempt = certify_extinction_v3(request, prefix.final_state, post_apply_events_boundary=True)
    assert not attempt["closed"]
    assert "susceptible_temperature_outside_cooling_domain" in attempt["rejection_reasons"]
    complete = run_v3_simulation(request, retain_snapshots=False)
    assert complete.final_state.ignition_time_s[0, 1] == pytest.approx(168.1)


def test_current_threshold_ready_node_is_rejected_even_with_nonpositive_heat(small):
    request = small._request(
        ("grass", "grass"), edge_weight=0.08, heat_loss_W_m2_K=1, end_time_s=30
    )
    pilot = request.piloted_ignition_mask.copy()
    pilot[0, 1] = False
    masked = replace(request, piloted_ignition_mask=pilot)
    prefix = no_burning_prefix(masked)
    assert (
        prefix.final_state.surface_temperature_K[0, 1]
        >= request.parameters.ignition_temperature_K[0, 1]
    )
    attempt = certify_extinction_v3(request, prefix.final_state, post_apply_events_boundary=True)
    assert not attempt["closed"]
    assert (
        "thermal_ignition_already_eligible_at_current_temperature" in attempt["rejection_reasons"]
    )


def test_unconfirmed_boundary_and_firebrands_are_rejected(small):
    request = small._request(("grass",), end_time_s=10)
    prefix = no_burning_prefix(request)
    attempt = certify_extinction_v3(request, prefix.final_state)
    assert not attempt["closed"]
    assert "not_a_confirmed_post_apply_events_step_boundary" in attempt["rejection_reasons"]
    module = load("extinction_firebrands", Path(__file__).with_name("test_firebrands_v3.py"))
    _, parameters = module.request()
    brands = replace(request, firebrands=parameters)
    attempt = certify_extinction_v3(brands, prefix.final_state, post_apply_events_boundary=True)
    assert not attempt["closed"]
    assert "firebrands_or_delayed_ember_ignitions_not_supported" in attempt["rejection_reasons"]


def test_historical_peak_and_state_corruption_cannot_create_a_certificate(small):
    request = small._request(("grass", "grass"), end_time_s=10)
    prefix = no_burning_prefix(request)
    state = prefix.final_state.copy()
    bad_peak = np.ones(request.grid.shape)
    attempt = certify_extinction_v3(
        request,
        state,
        post_apply_events_boundary=True,
        maximum_valid_surface_temperature_K=bad_peak,
    )
    assert not attempt["closed"]
    assert "historical_temperature_maximum_inconsistent" in attempt["rejection_reasons"]
    state.fuel_remaining_kg[0, 0] = 1
    attempt = certify_extinction_v3(request, state, post_apply_events_boundary=True)
    assert not attempt["closed"] and "invalid_remaining_fuel" in attempt["rejection_reasons"]
