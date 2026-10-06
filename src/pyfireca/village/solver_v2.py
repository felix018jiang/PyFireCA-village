"""Synchronous reduced-order thermal solver for effective_exposure_ca_v2."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v2 import (
    V2CellPhysicsArrays,
    advance_surface_heating_and_evaporation,
    burn_interval_energy_and_hrr,
    monotone_thermal_timestep_limit_s,
    net_surface_heat_flux_W_m2,
    radiant_flux_from_edges,
)
from pyfireca.village.state import VillageCellState


@dataclass(frozen=True, slots=True)
class V2Scenario:
    max_dt_s: float
    end_time_s: float
    ambient_temperature_K: float
    ignitions: tuple[VillageIgnitionEvent, ...]

    def __post_init__(self) -> None:
        for name in ("max_dt_s", "end_time_s", "ambient_temperature_K"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        if not self.ignitions:
            raise ValueError("v2 requires at least one direct ignition")


@dataclass(frozen=True, slots=True)
class V2SimulationRequest:
    grid: VillageGridSpec
    burnable_mask: NDArray[np.bool_]
    parameters: V2CellPhysicsArrays
    exposure_edges: ExposureEdges
    piloted_ignition_mask: NDArray[np.bool_]
    scenario: V2Scenario
    sample_interval_s: float | None = None

    def __post_init__(self) -> None:
        shape = self.grid.shape
        burnable = np.asarray(self.burnable_mask)
        pilot = np.asarray(self.piloted_ignition_mask)
        if burnable.shape != shape or burnable.dtype != np.bool_:
            raise TypeError("v2 burnable_mask must be boolean and match the grid")
        if pilot.shape != shape or pilot.dtype != np.bool_:
            raise TypeError("v2 piloted_ignition_mask must be boolean and match the grid")
        if self.exposure_edges.cell_count != self.grid.height * self.grid.width:
            raise ValueError("v2 exposure graph does not match the grid")
        self.parameters.validated(shape, burnable, cell_area_m2=self.grid.cell_area_m2)
        if self.sample_interval_s is not None and (
            not isfinite(self.sample_interval_s) or self.sample_interval_s <= 0.0
        ):
            raise ValueError("v2 sample_interval_s must be finite and positive")
        for event in self.scenario.ignitions:
            event.validate(self.grid, self.scenario.end_time_s)
            if not burnable[event.row, event.col]:
                raise ValueError("v2 direct ignition must target a burnable cell")


@dataclass(slots=True)
class V2State:
    time_s: float
    step_index: int
    state: NDArray[np.uint8]
    temperature_K: NDArray[np.float64]
    water_mass_kg_m2: NDArray[np.float64]
    incident_dose_J_m2: NDArray[np.float64]
    fuel_remaining_kg: NDArray[np.float64]
    burn_age_s: NDArray[np.float64]
    hrr_W: NDArray[np.float64]
    ignition_time_s: NDArray[np.float64]
    burnout_time_s: NDArray[np.float64]

    def copy(self) -> V2State:
        return V2State(
            time_s=self.time_s,
            step_index=self.step_index,
            state=self.state.copy(),
            temperature_K=self.temperature_K.copy(),
            water_mass_kg_m2=self.water_mass_kg_m2.copy(),
            incident_dose_J_m2=self.incident_dose_J_m2.copy(),
            fuel_remaining_kg=self.fuel_remaining_kg.copy(),
            burn_age_s=self.burn_age_s.copy(),
            hrr_W=self.hrr_W.copy(),
            ignition_time_s=self.ignition_time_s.copy(),
            burnout_time_s=self.burnout_time_s.copy(),
        )


@dataclass(frozen=True, slots=True)
class V2Snapshot:
    time_s: float
    state: NDArray[np.uint8]
    temperature_K: NDArray[np.float64]
    water_mass_kg_m2: NDArray[np.float64]
    fuel_remaining_kg: NDArray[np.float64]
    hrr_W: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class V2SimulationResult:
    final_state: V2State
    initial_fuel_kg: NDArray[np.float64]
    ignition_cause: NDArray[np.uint8]
    snapshots: tuple[V2Snapshot, ...]

    @property
    def consumed_fuel_kg(self) -> float:
        return float(np.sum(self.initial_fuel_kg - self.final_state.fuel_remaining_kg))


def _initial_state(request: V2SimulationRequest) -> V2State:
    shape = request.grid.shape
    burnable = request.burnable_mask
    parameters = request.parameters
    state = np.full(shape, int(VillageCellState.INERT), dtype=np.uint8)
    state[burnable] = int(VillageCellState.UNBURNED)
    fuel = (
        parameters.fuel_load_kg_m2
        * request.grid.cell_area_m2
        * parameters.pyrolysable_dry_mass_fraction
    )
    fuel = np.where(burnable, fuel, 0.0)
    water = parameters.effective_heated_dry_mass_kg_m2 * parameters.moisture_content_dry_basis
    water = np.where(burnable, water, 0.0)
    return V2State(
        time_s=0.0,
        step_index=0,
        state=state,
        temperature_K=np.full(shape, request.scenario.ambient_temperature_K, dtype=np.float64),
        water_mass_kg_m2=water,
        incident_dose_J_m2=np.zeros(shape),
        fuel_remaining_kg=fuel,
        burn_age_s=np.zeros(shape),
        hrr_W=np.zeros(shape),
        ignition_time_s=np.full(shape, np.inf),
        burnout_time_s=np.full(shape, np.inf),
    )


def run_v2_simulation(request: V2SimulationRequest) -> V2SimulationResult:
    """Run the v2 thermal model with synchronous cell-state commits."""

    if not isinstance(request, V2SimulationRequest):
        raise TypeError("request must be V2SimulationRequest")
    state = _initial_state(request)
    initial_fuel = state.fuel_remaining_kg.copy()
    parameters = request.parameters
    scenario = request.scenario
    cause = np.zeros(request.grid.shape, dtype=np.uint8)  # 1 direct, 2 thermal exposure
    events = sorted(
        scenario.ignitions,
        key=lambda event: (event.time_s, request.grid.cell_id(event.row, event.col)),
    )
    event_index = 0
    tolerance = 1e-12
    snapshots: list[V2Snapshot] = []
    next_sample = 0.0 if request.sample_interval_s is not None else np.inf

    def apply_events() -> None:
        nonlocal event_index
        while event_index < len(events) and events[event_index].time_s <= state.time_s + tolerance:
            event = events[event_index]
            if state.state[event.row, event.col] in {
                int(VillageCellState.UNBURNED),
                int(VillageCellState.HEATING),
            }:
                state.state[event.row, event.col] = int(VillageCellState.BURNING)
                state.temperature_K[event.row, event.col] = max(
                    state.temperature_K[event.row, event.col],
                    parameters.ignition_temperature_K[event.row, event.col],
                )
                state.ignition_time_s[event.row, event.col] = state.time_s
                cause[event.row, event.col] = 1
            event_index += 1

    def capture() -> None:
        nonlocal next_sample
        if request.sample_interval_s is None or state.time_s + tolerance < next_sample:
            return
        snapshots.append(
            V2Snapshot(
                time_s=state.time_s,
                state=state.state.copy(),
                temperature_K=state.temperature_K.copy(),
                water_mass_kg_m2=state.water_mass_kg_m2.copy(),
                fuel_remaining_kg=state.fuel_remaining_kg.copy(),
                hrr_W=state.hrr_W.copy(),
            )
        )
        while next_sample <= state.time_s + tolerance:
            next_sample += request.sample_interval_s

    while state.time_s < scenario.end_time_s - tolerance:
        apply_events()
        capture()
        next_event = events[event_index].time_s if event_index < len(events) else np.inf
        thermal_limit = monotone_thermal_timestep_limit_s(
            state.temperature_K,
            parameters.effective_heated_dry_mass_kg_m2,
            parameters.specific_heat_J_kg_K,
            parameters.emissivity,
            parameters.ambient_heat_transfer_coefficient_W_m2_K,
            parameters.backing_conductance_W_m2_K,
        )
        dt_s = min(
            scenario.max_dt_s,
            scenario.end_time_s - state.time_s,
            next_event - state.time_s,
            thermal_limit,
        )
        if not isfinite(dt_s) or dt_s <= tolerance:
            raise RuntimeError("v2 solver selected a non-positive time step")
        burning = state.state == int(VillageCellState.BURNING)
        peak_hrr = parameters.peak_hrrpua_W_m2 * parameters.active_surface_area_m2
        _energy, mass_loss, hrr = burn_interval_energy_and_hrr(
            burning,
            state.burn_age_s,
            state.fuel_remaining_kg,
            peak_hrr,
            parameters.growth_time_s,
            parameters.steady_time_s,
            parameters.decay_time_s,
            parameters.heat_of_combustion_J_kg,
            dt_s,
        )
        radiant_flux = radiant_flux_from_edges(
            hrr,
            parameters.radiative_fraction,
            parameters.receiver_area_m2,
            request.exposure_edges,
        )
        susceptible = (state.state == int(VillageCellState.UNBURNED)) | (
            state.state == int(VillageCellState.HEATING)
        )
        net_flux = net_surface_heat_flux_W_m2(
            state.temperature_K,
            radiant_flux,
            np.zeros(request.grid.shape),
            parameters.absorptivity,
            parameters.emissivity,
            parameters.ambient_heat_transfer_coefficient_W_m2_K,
            parameters.backing_conductance_W_m2_K,
            scenario.ambient_temperature_K,
        )
        next_temperature = state.temperature_K.copy()
        next_water = state.water_mass_kg_m2.copy()
        heated_temperature, heated_water = advance_surface_heating_and_evaporation(
            state.temperature_K[susceptible],
            state.water_mass_kg_m2[susceptible],
            parameters.effective_heated_dry_mass_kg_m2[susceptible],
            parameters.specific_heat_J_kg_K[susceptible],
            net_flux[susceptible],
            dt_s,
            ambient_temperature_K=scenario.ambient_temperature_K,
        )
        next_temperature[susceptible] = heated_temperature
        next_water[susceptible] = heated_water
        next_fuel = np.maximum(state.fuel_remaining_kg - mass_loss, 0.0)
        next_state = state.state.copy()
        exhausted = burning & (next_fuel <= 1e-12)
        next_state[exhausted] = int(VillageCellState.BURNED_OUT)
        newly_ignited = (
            susceptible
            & request.burnable_mask
            & request.piloted_ignition_mask
            & (next_fuel > 1e-12)
            & (next_temperature >= parameters.ignition_temperature_K)
        )
        next_state[newly_ignited] = int(VillageCellState.BURNING)
        remaining = susceptible & ~newly_ignited
        next_state[remaining] = int(VillageCellState.UNBURNED)
        next_state[remaining & (next_temperature > scenario.ambient_temperature_K + 1e-9)] = int(
            VillageCellState.HEATING
        )
        new_time = state.time_s + dt_s
        next_burn_age = state.burn_age_s.copy()
        next_burn_age[burning] += dt_s
        next_burn_age[newly_ignited] = 0.0
        state.state = next_state
        state.temperature_K = next_temperature
        state.water_mass_kg_m2 = next_water
        state.incident_dose_J_m2 += np.maximum(parameters.absorptivity * radiant_flux, 0.0) * dt_s
        state.fuel_remaining_kg = next_fuel
        state.burn_age_s = next_burn_age
        state.hrr_W = hrr
        state.ignition_time_s[newly_ignited] = new_time
        cause[newly_ignited & (cause == 0)] = 2
        state.burnout_time_s[exhausted] = new_time
        state.time_s = new_time
        state.step_index += 1
        apply_events()
    capture()
    if request.sample_interval_s is not None and (
        not snapshots or abs(snapshots[-1].time_s - state.time_s) > tolerance
    ):
        snapshots.append(
            V2Snapshot(
                time_s=state.time_s,
                state=state.state.copy(),
                temperature_K=state.temperature_K.copy(),
                water_mass_kg_m2=state.water_mass_kg_m2.copy(),
                fuel_remaining_kg=state.fuel_remaining_kg.copy(),
                hrr_W=state.hrr_W.copy(),
            )
        )
    return V2SimulationResult(
        final_state=state.copy(),
        initial_fuel_kg=initial_fuel,
        ignition_cause=cause,
        snapshots=tuple(snapshots),
    )


__all__ = [
    "V2Scenario",
    "V2SimulationRequest",
    "V2SimulationResult",
    "V2Snapshot",
    "V2State",
    "run_v2_simulation",
]
