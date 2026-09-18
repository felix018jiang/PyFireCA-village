from __future__ import annotations

import numpy as np
import pytest

from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v2 import (
    V2CellPhysicsArrays,
    advance_surface_heating_and_evaporation,
    burn_interval_energy_and_hrr,
    energy_consistent_steady_time_s,
    moisture_dry_to_wet_basis,
    moisture_wet_to_dry_basis,
    net_surface_heat_flux_W_m2,
    radiant_flux_from_edges,
)
from pyfireca.village.solver_v2 import V2Scenario, V2SimulationRequest, run_v2_simulation
from pyfireca.village.state import VillageCellState


def test_moisture_basis_round_trip() -> None:
    wet = moisture_dry_to_wet_basis(0.25)
    assert wet == pytest.approx(0.2)
    assert moisture_wet_to_dry_basis(wet) == pytest.approx(0.25)


def test_heating_evaporates_water_before_dry_temperature_rises_above_boiling() -> None:
    initial_temperature = np.array([293.15])
    initial_water = np.array([0.1])
    dry_mass = np.array([1.0])
    dry_heat = np.array([1000.0])
    energy_to_boiling = (1000.0 + 0.1 * 4180.0) * 80.0
    evaporation_energy = 0.1 * 2_256_400.0
    supplied_energy = energy_to_boiling + evaporation_energy + 10_000.0

    temperature, water = advance_surface_heating_and_evaporation(
        initial_temperature,
        initial_water,
        dry_mass,
        dry_heat,
        np.array([supplied_energy]),
        1.0,
        ambient_temperature_K=293.15,
    )

    assert water[0] == pytest.approx(0.0, abs=1e-12)
    assert temperature[0] == pytest.approx(383.15)


def test_cooling_does_not_restore_evaporated_water() -> None:
    temperature, water = advance_surface_heating_and_evaporation(
        np.array([350.0]),
        np.array([0.02]),
        np.array([1.0]),
        np.array([1000.0]),
        np.array([-10_000.0]),
        1.0,
        ambient_temperature_K=293.15,
    )
    assert temperature[0] < 350.0
    assert water[0] == pytest.approx(0.02)


def test_burn_curve_integral_matches_fuel_energy_budget() -> None:
    steady = energy_consistent_steady_time_s(
        fuel_mass_kg=1.0,
        heat_of_combustion_J_kg=100.0,
        peak_hrr_W=10.0,
        growth_time_s=3.0,
        decay_time_s=2.0,
    )
    assert steady == pytest.approx(8.0)
    released, mass_loss, mean_hrr = burn_interval_energy_and_hrr(
        np.array([True]),
        np.array([0.0]),
        np.array([1.0]),
        np.array([10.0]),
        np.array([3.0]),
        np.array([steady]),
        np.array([2.0]),
        np.array([100.0]),
        13.0,
    )
    assert released[0] == pytest.approx(100.0)
    assert mass_loss[0] == pytest.approx(1.0)
    assert mean_hrr[0] == pytest.approx(100.0 / 13.0)


def test_surface_balance_and_radiation_transport_keep_area_basis_explicit() -> None:
    edges = ExposureEdges(
        source=np.array([0], dtype=np.int32),
        target=np.array([1], dtype=np.int32),
        weight=np.array([0.5]),
        cell_count=2,
    )
    flux = radiant_flux_from_edges(
        np.array([100.0, 0.0]),
        np.array([0.2, 0.2]),
        np.array([2.0, 2.0]),
        edges,
    )
    np.testing.assert_allclose(flux, [0.0, 5.0])
    net = net_surface_heat_flux_W_m2(
        np.array([293.15]),
        np.array([100.0]),
        np.array([0.0]),
        np.array([0.8]),
        np.array([0.9]),
        np.array([10.0]),
        np.array([5.0]),
        293.15,
    )
    assert net[0] == pytest.approx(80.0)


def test_v2_parameter_validation_rejects_curve_energy_mismatch() -> None:
    values = np.ones((1,), dtype=np.float64)
    parameters = V2CellPhysicsArrays(
        fuel_load_kg_m2=values,
        moisture_content_dry_basis=np.zeros((1,)),
        heat_of_combustion_J_kg=100.0 * values,
        ignition_temperature_K=600.0 * values,
        peak_hrrpua_W_m2=10.0 * values,
        radiative_fraction=0.2 * values,
        absorptivity=0.8 * values,
        growth_time_s=3.0 * values,
        steady_time_s=8.0 * values,
        decay_time_s=2.0 * values,
        effective_heated_dry_mass_kg_m2=values,
        specific_heat_J_kg_K=1000.0 * values,
        emissivity=0.9 * values,
        ambient_heat_transfer_coefficient_W_m2_K=10.0 * values,
        backing_conductance_W_m2_K=5.0 * values,
        receiver_area_m2=values,
        active_surface_area_m2=values,
        pyrolysable_dry_mass_fraction=values,
    )
    parameters.validated((1,), np.array([True]), cell_area_m2=1.0)
    inconsistent = V2CellPhysicsArrays(
        **{
            name: (2.0 * value if name == "steady_time_s" else value)
            for name, value in (
                (field, getattr(parameters, field)) for field in parameters.__dataclass_fields__
            )
        }
    )
    with pytest.raises(ValueError, match="energy does not match"):
        inconsistent.validated((1,), np.array([True]), cell_area_m2=1.0)


def test_v2_solver_ignites_target_from_prior_step_radiation() -> None:
    shape = (1, 2)
    ones = np.ones(shape)
    parameters = V2CellPhysicsArrays(
        fuel_load_kg_m2=ones,
        moisture_content_dry_basis=np.zeros(shape),
        heat_of_combustion_J_kg=1000.0 * ones,
        ignition_temperature_K=302.0 * ones,
        peak_hrrpua_W_m2=100.0 * ones,
        radiative_fraction=ones,
        absorptivity=ones,
        growth_time_s=3.0 * ones,
        steady_time_s=8.0 * ones,
        decay_time_s=2.0 * ones,
        effective_heated_dry_mass_kg_m2=0.01 * ones,
        specific_heat_J_kg_K=100.0 * ones,
        emissivity=np.zeros(shape),
        ambient_heat_transfer_coefficient_W_m2_K=np.zeros(shape),
        backing_conductance_W_m2_K=np.zeros(shape),
        receiver_area_m2=ones,
        active_surface_area_m2=ones,
        pyrolysable_dry_mass_fraction=ones,
    )
    edges = ExposureEdges(
        source=np.array([0], dtype=np.int32),
        target=np.array([1], dtype=np.int32),
        weight=np.array([1.0]),
        cell_count=2,
    )
    request = V2SimulationRequest(
        grid=VillageGridSpec(1, 2, 1.0, 0.0, 1.0),
        burnable_mask=np.ones(shape, dtype=np.bool_),
        parameters=parameters,
        exposure_edges=edges,
        piloted_ignition_mask=np.ones(shape, dtype=np.bool_),
        scenario=V2Scenario(
            max_dt_s=1.0,
            end_time_s=1.0,
            ambient_temperature_K=300.0,
            ignitions=(VillageIgnitionEvent(0.0, 0, 0, "direct"),),
        ),
        sample_interval_s=1.00000000001,
    )

    result = run_v2_simulation(request)

    assert result.final_state.state[0, 1] == int(VillageCellState.BURNING)
    assert result.final_state.ignition_time_s[0, 1] == pytest.approx(1.0)
    assert result.ignition_cause[0, 1] == 2
    assert result.snapshots[0].state[0, 1] == int(VillageCellState.UNBURNED)
    assert result.snapshots[-1].time_s == pytest.approx(1.0)
    assert np.array_equal(result.snapshots[-1].state, result.final_state.state)
