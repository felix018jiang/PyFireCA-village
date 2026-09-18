"""Synchronous finite-fuel solver for the version-1 village CA."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.combustion import (
    burning_mass_loss_and_hrr,
    maximum_fuel_limited_step_s,
)
from pyfireca.village.domain import ExposureEdges, PreparedVillageDomain
from pyfireca.village.exposure import integrate_exposure_dose, target_power_W
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.state import (
    VillageCellState,
    VillageState,
    build_initial_village_state,
)


@dataclass(frozen=True, slots=True)
class VillageScenario:
    max_dt_s: float
    end_time_s: float
    heating_display_fraction: float
    ignitions: tuple[VillageIgnitionEvent, ...]

    def __post_init__(self) -> None:
        if not isfinite(self.max_dt_s) or self.max_dt_s <= 0.0:
            raise ValueError("max_dt_s must be finite and positive")
        if not isfinite(self.end_time_s) or self.end_time_s <= 0.0:
            raise ValueError("end_time_s must be finite and positive")
        if not isfinite(self.heating_display_fraction) or not (
            0.0 < self.heating_display_fraction < 1.0
        ):
            raise ValueError("heating_display_fraction must lie in (0, 1)")
        if not self.ignitions:
            raise ValueError("at least one ignition event is required")


@dataclass(frozen=True, slots=True)
class VillageSimulationRequest:
    domain: PreparedVillageDomain
    scenario: VillageScenario
    exposure_edges: ExposureEdges
    sample_interval_s: float | None = None

    def __post_init__(self) -> None:
        expected = self.domain.grid.height * self.domain.grid.width
        if self.exposure_edges.cell_count != expected:
            raise ValueError("exposure edge cell_count does not match domain")
        if self.sample_interval_s is not None and (
            not isfinite(self.sample_interval_s) or self.sample_interval_s <= 0.0
        ):
            raise ValueError("sample_interval_s must be finite and positive")
        seen: set[tuple[float, int]] = set()
        for event in self.scenario.ignitions:
            event.validate(self.domain.grid, self.scenario.end_time_s)
            cell_id = self.domain.grid.cell_id(event.row, event.col)
            if not self.domain.burnable_mask[event.row, event.col]:
                raise ValueError(f"ignition cell {event.row, event.col} is not known burnable fuel")
            key = event.time_s, cell_id
            if key in seen:
                raise ValueError(f"duplicate ignition event at time/cell {key}")
            seen.add(key)


@dataclass(frozen=True, slots=True)
class VillageSnapshot:
    time_s: float
    state: NDArray[np.uint8]
    fuel_remaining_kg: NDArray[np.float64]
    exposure_J_m2: NDArray[np.float64]


@dataclass(frozen=True, slots=True)
class VillageSimulationResult:
    final_state: VillageState
    initial_fuel_kg: NDArray[np.float64]
    ignition_cause: NDArray[np.uint8]
    main_loop_stop_time_s: float
    stop_reason: str
    snapshots: tuple[VillageSnapshot, ...]

    @property
    def consumed_fuel_kg(self) -> float:
        return float(np.sum(self.initial_fuel_kg - self.final_state.fuel_remaining_kg))


def _apply_ignition(state: VillageState, event: VillageIgnitionEvent) -> None:
    if state.state[event.row, event.col] in {
        int(VillageCellState.UNBURNED),
        int(VillageCellState.HEATING),
    }:
        state.state[event.row, event.col] = int(VillageCellState.BURNING)
        state.ignition_time_s[event.row, event.col] = state.time_s


def _apply_due_ignitions(
    state: VillageState,
    events: list[VillageIgnitionEvent],
    event_index: int,
    cause: NDArray[np.uint8],
    *,
    tolerance: float,
) -> int:
    """Apply events through the current time and give external ignition precedence."""

    while event_index < len(events) and events[event_index].time_s <= state.time_s + tolerance:
        event = events[event_index]
        _apply_ignition(state, event)
        if state.ignition_time_s[event.row, event.col] >= state.time_s - tolerance:
            cause[event.row, event.col] = 1
        event_index += 1
    return event_index


def run_village_simulation(request: VillageSimulationRequest) -> VillageSimulationResult:
    """Run one deterministic synchronous village-fire simulation."""

    if not isinstance(request, VillageSimulationRequest):
        raise TypeError("request must be VillageSimulationRequest")
    domain = request.domain
    scenario = request.scenario
    state = build_initial_village_state(domain)
    initial_fuel = state.fuel_remaining_kg.copy()
    cause = np.zeros(domain.grid.shape, dtype=np.uint8)  # 1 external, 2 exposure
    events = sorted(
        request.scenario.ignitions,
        key=lambda event: (event.time_s, domain.grid.cell_id(event.row, event.col)),
    )
    event_index = 0
    mass_loss_rate = (
        domain.parameters.mass_loss_flux_kg_m2_s
        * domain.parameters.active_area_ratio
        * domain.grid.cell_area_m2
    )
    stop_reason = "end_time"
    main_loop_stop = scenario.end_time_s
    tolerance = 1e-12
    snapshots: list[VillageSnapshot] = []
    next_sample = 0.0 if request.sample_interval_s is not None else np.inf

    def capture_due_sample() -> None:
        nonlocal next_sample
        if request.sample_interval_s is None or state.time_s + tolerance < next_sample:
            return
        if not snapshots or abs(snapshots[-1].time_s - state.time_s) > tolerance:
            snapshots.append(
                VillageSnapshot(
                    time_s=state.time_s,
                    state=state.state.copy(),
                    fuel_remaining_kg=state.fuel_remaining_kg.copy(),
                    exposure_J_m2=state.exposure_J_m2.copy(),
                )
            )
        while next_sample <= state.time_s + tolerance:
            next_sample += request.sample_interval_s

    while state.time_s < scenario.end_time_s - tolerance:
        event_index = _apply_due_ignitions(state, events, event_index, cause, tolerance=tolerance)
        capture_due_sample()

        burning = state.state == int(VillageCellState.BURNING)
        if not np.any(burning) and event_index >= len(events):
            stop_reason = "no_sources_or_future_ignitions"
            main_loop_stop = state.time_s
            remaining = scenario.end_time_s - state.time_s
            if remaining > tolerance:
                susceptible = (state.state == int(VillageCellState.UNBURNED)) | (
                    state.state == int(VillageCellState.HEATING)
                )
                state.exposure_J_m2[susceptible] *= np.exp(
                    -remaining / domain.parameters.exposure_decay_s[susceptible]
                )
                heating = susceptible & (
                    state.exposure_J_m2
                    >= scenario.heating_display_fraction * domain.parameters.ignition_dose_J_m2
                )
                state.state[susceptible] = int(VillageCellState.UNBURNED)
                state.state[heating] = int(VillageCellState.HEATING)
                state.time_s = scenario.end_time_s
                capture_due_sample()
            break

        next_event = events[event_index].time_s if event_index < len(events) else np.inf
        dt_s = min(
            scenario.max_dt_s,
            scenario.end_time_s - state.time_s,
            next_event - state.time_s,
            maximum_fuel_limited_step_s(burning, state.fuel_remaining_kg, mass_loss_rate),
        )
        if not isfinite(dt_s) or dt_s <= tolerance:
            raise RuntimeError("the solver selected a non-positive time step")

        loss, hrr = burning_mass_loss_and_hrr(
            burning,
            state.fuel_remaining_kg,
            mass_loss_rate,
            domain.parameters.heat_of_combustion_J_kg,
            dt_s,
        )
        emitted = hrr * domain.parameters.emission_fraction
        received = target_power_W(emitted, request.exposure_edges).reshape(domain.grid.shape)
        flux = received * domain.parameters.absorptivity / domain.grid.cell_area_m2
        susceptible = (state.state == int(VillageCellState.UNBURNED)) | (
            state.state == int(VillageCellState.HEATING)
        )
        next_exposure = state.exposure_J_m2.copy()
        next_exposure[susceptible] = integrate_exposure_dose(
            state.exposure_J_m2[susceptible],
            flux[susceptible],
            domain.parameters.exposure_decay_s[susceptible],
            dt_s,
        )
        next_fuel = state.fuel_remaining_kg - loss
        next_fuel[np.abs(next_fuel) < tolerance] = 0.0
        next_state = state.state.copy()
        exhausted = burning & (next_fuel <= tolerance)
        next_state[exhausted] = int(VillageCellState.BURNED_OUT)
        newly_ignited = susceptible & (next_exposure >= domain.parameters.ignition_dose_J_m2)
        next_state[newly_ignited] = int(VillageCellState.BURNING)
        remaining_susceptible = susceptible & ~newly_ignited
        heating = remaining_susceptible & (
            next_exposure
            >= scenario.heating_display_fraction * domain.parameters.ignition_dose_J_m2
        )
        next_state[remaining_susceptible] = int(VillageCellState.UNBURNED)
        next_state[heating] = int(VillageCellState.HEATING)
        new_time = state.time_s + dt_s
        state.state = next_state
        state.fuel_remaining_kg = next_fuel
        state.exposure_J_m2 = next_exposure
        state.hrr_W = hrr
        state.ignition_time_s[newly_ignited] = new_time
        cause[newly_ignited & (cause == 0)] = 2
        state.burnout_time_s[exhausted] = new_time
        state.time_s = new_time
        state.step_index += 1
        event_index = _apply_due_ignitions(state, events, event_index, cause, tolerance=tolerance)
        capture_due_sample()
        state.validate(domain)

    state.validate(domain)
    if request.sample_interval_s is not None and (
        not snapshots or abs(snapshots[-1].time_s - state.time_s) > tolerance
    ):
        snapshots.append(
            VillageSnapshot(
                time_s=state.time_s,
                state=state.state.copy(),
                fuel_remaining_kg=state.fuel_remaining_kg.copy(),
                exposure_J_m2=state.exposure_J_m2.copy(),
            )
        )
    return VillageSimulationResult(
        final_state=state.copy(),
        initial_fuel_kg=initial_fuel,
        ignition_cause=cause,
        main_loop_stop_time_s=main_loop_stop,
        stop_reason=stop_reason,
        snapshots=tuple(snapshots),
    )
