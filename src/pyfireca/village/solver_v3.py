"""Synchronous building--vegetation solver for ``effective_exposure_ca_v3``.

The solver consumes the per-cell quantities prepared by V03/M03 and the one
shared radiation graph prepared by V04.  Material and building identifiers are
metadata only: every burnable cell follows the same heat, ignition, finite-fuel
and state-transition equations.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from math import isfinite
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.exposure import SourceIndexedExposure
from pyfireca.village.flame_contact_v3 import FlameContactOperator, FlameContactParameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v2 import (
    advance_surface_heating_and_evaporation,
    burn_interval_energy_and_hrr,
    monotone_thermal_timestep_limit_s,
    net_surface_heat_flux_W_m2,
)
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.state import VillageCellState

IGNITION_CAUSE_NONE = np.uint8(0)
IGNITION_CAUSE_DIRECT = np.uint8(1)
IGNITION_CAUSE_THERMAL = np.uint8(2)


def _mask(name: str, value: NDArray[np.bool_], shape: tuple[int, int]) -> NDArray[np.bool_]:
    array = np.asarray(value)
    if array.shape != shape or array.dtype != np.bool_:
        raise TypeError(f"{name} must be a boolean array matching the grid")
    return array.copy()


def _index_array(
    name: str, value: NDArray[np.integer], shape: tuple[int, int]
) -> NDArray[np.int32]:
    array = np.asarray(value)
    if array.shape != shape or not np.issubdtype(array.dtype, np.integer):
        raise TypeError(f"{name} must be an integer array matching the grid")
    return array.astype(np.int32, copy=True)


@dataclass(frozen=True, slots=True)
class V3Scenario:
    """Numerical clock and ignition events for one V3 run."""

    max_dt_s: float
    end_time_s: float
    ambient_temperature_K: float
    heating_temperature_delta_K: float
    ignitions: tuple[VillageIgnitionEvent, ...]

    def __post_init__(self) -> None:
        for name in ("max_dt_s", "end_time_s", "ambient_temperature_K"):
            value = getattr(self, name)
            if not isfinite(value) or value <= 0.0:
                raise ValueError(f"{name} must be finite and positive")
        if not isfinite(self.heating_temperature_delta_K) or self.heating_temperature_delta_K < 0.0:
            raise ValueError("heating_temperature_delta_K must be finite and non-negative")
        if not self.ignitions:
            raise ValueError("v3 requires at least one direct ignition")


@dataclass(frozen=True, slots=True)
class V3SimulationRequest:
    """Fully resolved V3 arrays; no material defaults are selected here."""

    grid: VillageGridSpec
    domain_mask: NDArray[np.bool_]
    known_mask: NDArray[np.bool_]
    burnable_mask: NDArray[np.bool_]
    source_mask: NDArray[np.bool_]
    target_mask: NDArray[np.bool_]
    piloted_ignition_mask: NDArray[np.bool_]
    material_index: NDArray[np.integer]
    landcover_index: NDArray[np.integer]
    building_index: NDArray[np.integer]
    parameters: V3CellPhysicsArrays
    exposure_edges: ExposureEdges
    scenario: V3Scenario
    unknown_policy: str = "error"
    sample_interval_s: float | None = None
    ground_elevation_m: NDArray[np.floating] | None = None
    flame_contact: FlameContactParameters | None = None
    material_evidence_index: NDArray[np.uint8] | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.grid, VillageGridSpec):
            raise TypeError("grid must be VillageGridSpec")
        if not isinstance(self.parameters, V3CellPhysicsArrays):
            raise TypeError("parameters must be V3CellPhysicsArrays")
        if not isinstance(self.exposure_edges, ExposureEdges):
            raise TypeError("exposure_edges must be ExposureEdges")
        if not isinstance(self.scenario, V3Scenario):
            raise TypeError("scenario must be V3Scenario")
        if self.unknown_policy not in {"error", "exclude"}:
            raise ValueError("unknown_policy must be 'error' or 'exclude'")
        if self.sample_interval_s is not None and (
            not isfinite(self.sample_interval_s) or self.sample_interval_s <= 0.0
        ):
            raise ValueError("sample_interval_s must be finite and positive")

        if self.flame_contact is not None:
            if not isinstance(self.flame_contact, FlameContactParameters):
                raise TypeError("flame_contact must be FlameContactParameters or None")
            if self.grid.cell_size_m > self.flame_contact.max_cell_size_m + 1e-12:
                raise ValueError("flame contact grid exceeds its explicit maximum cell size")
        shape = self.grid.shape
        if self.material_evidence_index is not None:
            evidence = np.asarray(self.material_evidence_index)
            if evidence.shape != shape or evidence.dtype != np.uint8:
                raise ValueError("material evidence must be uint8 and match the grid")
            if not np.all(np.isin(evidence, [0, 1, 2, 3, 255])):
                raise ValueError("unsupported material evidence code")
            evidence = evidence.copy()
            evidence.setflags(write=False)
            object.__setattr__(self, "material_evidence_index", evidence)
        if self.ground_elevation_m is not None:
            elevation = np.asarray(self.ground_elevation_m, dtype=np.float64)
            if elevation.shape != shape or not np.all(np.isfinite(elevation)):
                raise ValueError("ground_elevation_m must be finite and match the grid")
            object.__setattr__(self, "ground_elevation_m", elevation.copy())
        masks = {
            name: _mask(name, getattr(self, name), shape)
            for name in (
                "domain_mask",
                "known_mask",
                "burnable_mask",
                "source_mask",
                "target_mask",
                "piloted_ignition_mask",
            )
        }
        indexes = {
            name: _index_array(name, getattr(self, name), shape)
            for name in ("material_index", "landcover_index", "building_index")
        }
        domain = masks["domain_mask"]
        known = masks["known_mask"]
        burnable = masks["burnable_mask"]
        source = masks["source_mask"]
        target = masks["target_mask"]
        pilot = masks["piloted_ignition_mask"]
        unknown = domain & ~known
        if np.any(known & ~domain):
            raise ValueError("known cells must lie inside the v3 domain")
        if np.any(burnable & ~known):
            raise ValueError("burnable cells must be known domain cells")
        if np.any(source != burnable):
            raise ValueError("v3 source_mask must contain every and only burnable cell")
        if np.any(burnable & ~target):
            raise ValueError("every burnable cell must be a v3 transport target")
        if np.any(target & ~known):
            raise ValueError("v3 transport targets must be known domain cells")
        if np.any(pilot & ~burnable):
            raise ValueError("piloted ignition cells must be burnable")
        if self.unknown_policy == "error" and np.any(unknown):
            rows = np.argwhere(unknown)
            first = tuple(int(value) for value in rows[0])
            raise ValueError(
                f"UNKNOWN_CELLS: {len(rows)} domain cells are unresolved; first={first}"
            )
        if np.any(indexes["material_index"][known] < 0):
            raise ValueError("known v3 cells require a non-negative material index")
        if np.any(indexes["landcover_index"][known] < 0):
            raise ValueError("known v3 cells require a non-negative landcover index")
        if self.exposure_edges.cell_count != self.grid.height * self.grid.width:
            raise ValueError("v3 exposure graph does not match the grid")

        parameters = self.parameters.validated(shape, self.grid.cell_area_m2)
        positive_fields = (
            "covered_area_m2",
            "pyrolysable_dry_fuel_mass_kg",
            "heat_of_combustion_J_kg_dry",
            "ignition_temperature_K",
            "peak_hrr_W",
            "effective_heated_dry_mass_kg",
            "specific_heat_J_kg_K",
            "receiver_area_m2",
            "active_surface_area_m2",
            "source_height_m",
            "receiver_height_m",
        )
        for name in positive_fields:
            if np.any(getattr(parameters, name)[burnable] <= 0.0):
                raise ValueError(f"v3 parameter {name} must be positive in burnable cells")
        if np.any(parameters.growth_time_s[burnable] <= 0.0) or np.any(
            parameters.decay_time_s[burnable] <= 0.0
        ):
            raise ValueError("v3 growth and decay times must be positive in burnable cells")
        if np.any(
            parameters.effective_heated_dry_mass_kg[burnable]
            > parameters.initial_dry_fuel_mass_kg[burnable] + 1e-12
        ):
            raise ValueError("v3 effective heated dry mass cannot exceed total dry fuel mass")
        if not np.allclose(
            parameters.configured_curve_energy_J[burnable],
            parameters.available_energy_J[burnable],
            rtol=1e-9,
            atol=1e-6,
        ):
            raise ValueError("v3 burn curve must close exactly against available fuel energy")
        if np.any(parameters.receiver_area_m2[target] <= 0.0):
            raise ValueError("every v3 transport target requires positive receiver area")
        initial_surface_water = (
            parameters.effective_heated_dry_mass_kg * parameters.moisture_content_dry_basis
        )
        if np.any(initial_surface_water[burnable] > parameters.water_mass_kg[burnable] + 1e-12):
            raise ValueError("v3 effective heated water cannot exceed total initial water")

        flat_source = source.reshape(-1)
        flat_target = target.reshape(-1)
        if np.any(~flat_source[self.exposure_edges.source]):
            raise ValueError("v3 exposure graph contains an edge from a non-source cell")
        if np.any(~flat_target[self.exposure_edges.target]):
            raise ValueError("v3 exposure graph contains an edge to a non-target cell")
        for event in self.scenario.ignitions:
            event.validate(self.grid, self.scenario.end_time_s)
            if not burnable[event.row, event.col]:
                raise ValueError("v3 direct ignition must target a known burnable cell")

        for name, value in masks.items():
            object.__setattr__(self, name, value)
        for name, value in indexes.items():
            object.__setattr__(self, name, value)
        object.__setattr__(self, "parameters", parameters)


@dataclass(slots=True)
class V3State:
    time_s: float
    step_index: int
    state: NDArray[np.uint8]
    surface_temperature_K: NDArray[np.float64]
    temperature_valid_mask: NDArray[np.bool_]
    heated_water_remaining_kg: NDArray[np.float64]
    fuel_remaining_kg: NDArray[np.float64]
    burn_age_s: NDArray[np.float64]
    interval_mean_hrr_W: NDArray[np.float64]
    incident_radiation_J: NDArray[np.float64]
    absorbed_radiation_J: NDArray[np.float64]
    received_convection_J: NDArray[np.float64]
    released_energy_J: NDArray[np.float64]
    ignition_time_s: NDArray[np.float64]
    burnout_time_s: NDArray[np.float64]

    def copy(self) -> V3State:
        return V3State(
            time_s=self.time_s,
            step_index=self.step_index,
            state=self.state.copy(),
            surface_temperature_K=self.surface_temperature_K.copy(),
            temperature_valid_mask=self.temperature_valid_mask.copy(),
            heated_water_remaining_kg=self.heated_water_remaining_kg.copy(),
            fuel_remaining_kg=self.fuel_remaining_kg.copy(),
            burn_age_s=self.burn_age_s.copy(),
            interval_mean_hrr_W=self.interval_mean_hrr_W.copy(),
            incident_radiation_J=self.incident_radiation_J.copy(),
            absorbed_radiation_J=self.absorbed_radiation_J.copy(),
            received_convection_J=self.received_convection_J.copy(),
            released_energy_J=self.released_energy_J.copy(),
            ignition_time_s=self.ignition_time_s.copy(),
            burnout_time_s=self.burnout_time_s.copy(),
        )


@dataclass(frozen=True, slots=True)
class V3Snapshot:
    time_s: float
    state: NDArray[np.uint8]
    surface_temperature_K: NDArray[np.float64]
    temperature_valid_mask: NDArray[np.bool_]
    heated_water_remaining_kg: NDArray[np.float64]
    fuel_remaining_kg: NDArray[np.float64]
    interval_mean_hrr_W: NDArray[np.float64]


class V3SnapshotSink(Protocol):
    """Receive exact sampled frames without retaining them in solver memory."""

    def append(self, snapshot: V3Snapshot) -> None:
        """Persist one immutable snapshot."""


@dataclass(slots=True)
class V3EnergyBudget:
    released_J: float = 0.0
    radiative_emitted_J: float = 0.0
    non_radiative_released_J: float = 0.0
    radiative_intercepted_J: float = 0.0
    radiative_unintercepted_J: float = 0.0
    susceptible_incident_J: float = 0.0
    susceptible_absorbed_J: float = 0.0
    susceptible_unabsorbed_J: float = 0.0
    inert_received_J: float = 0.0
    inactive_combustible_received_J: float = 0.0
    convective_transferred_J: float = 0.0
    nonradiative_untransferred_J: float = 0.0
    energy_limited_contact_source_steps: int = 0


@dataclass(frozen=True, slots=True)
class V3SimulationResult:
    final_state: V3State
    initial_total_dry_fuel_kg: NDArray[np.float64]
    initial_fuel_kg: NDArray[np.float64]
    initial_total_water_kg: NDArray[np.float64]
    initial_heated_water_kg: NDArray[np.float64]
    ignition_cause: NDArray[np.uint8]
    energy_budget: V3EnergyBudget
    snapshots: tuple[V3Snapshot, ...]

    @property
    def consumed_fuel_kg(self) -> float:
        return float(np.sum(self.initial_fuel_kg - self.final_state.fuel_remaining_kg))


def _initial_state(request: V3SimulationRequest) -> V3State:
    shape = request.grid.shape
    parameters = request.parameters
    cell_state = np.full(shape, int(VillageCellState.OUTSIDE), dtype=np.uint8)
    unknown = request.domain_mask & ~request.known_mask
    inert = request.known_mask & ~request.burnable_mask
    cell_state[unknown] = int(VillageCellState.UNKNOWN)
    cell_state[inert] = int(VillageCellState.INERT)
    cell_state[request.burnable_mask] = int(VillageCellState.UNBURNED)
    surface_water = parameters.effective_heated_dry_mass_kg * parameters.moisture_content_dry_basis
    return V3State(
        time_s=0.0,
        step_index=0,
        state=cell_state,
        surface_temperature_K=np.full(
            shape, request.scenario.ambient_temperature_K, dtype=np.float64
        ),
        temperature_valid_mask=request.burnable_mask.copy(),
        heated_water_remaining_kg=np.where(request.burnable_mask, surface_water, 0.0),
        fuel_remaining_kg=np.where(
            request.burnable_mask, parameters.pyrolysable_dry_fuel_mass_kg, 0.0
        ),
        burn_age_s=np.zeros(shape, dtype=np.float64),
        interval_mean_hrr_W=np.zeros(shape, dtype=np.float64),
        incident_radiation_J=np.zeros(shape, dtype=np.float64),
        absorbed_radiation_J=np.zeros(shape, dtype=np.float64),
        received_convection_J=np.zeros(shape, dtype=np.float64),
        released_energy_J=np.zeros(shape, dtype=np.float64),
        ignition_time_s=np.full(shape, np.nan, dtype=np.float64),
        burnout_time_s=np.full(shape, np.nan, dtype=np.float64),
    )


def run_v3_simulation(
    request: V3SimulationRequest,
    *,
    snapshot_sink: V3SnapshotSink | None = None,
    retain_snapshots: bool = True,
    progress_callback: Callable[[float, float], None] | None = None,
) -> V3SimulationResult:
    """Run the V3 model with one synchronous clock for buildings and vegetation."""

    if not isinstance(request, V3SimulationRequest):
        raise TypeError("request must be V3SimulationRequest")
    contact = (
        FlameContactOperator.build(
            request.grid,
            request.burnable_mask,
            request.parameters,
            request.flame_contact,
            request.ground_elevation_m,
            request.material_index,
        )
        if request.flame_contact is not None
        else None
    )
    return _run_resolved_v3_simulation(
        request,
        contact=contact,
        snapshot_sink=snapshot_sink,
        retain_snapshots=retain_snapshots,
        progress_callback=progress_callback,
    )


def _run_resolved_v3_simulation(
    request: V3SimulationRequest,
    *,
    contact: FlameContactOperator | None,
    snapshot_sink: V3SnapshotSink | None = None,
    retain_snapshots: bool = True,
    progress_callback: Callable[[float, float], None] | None = None,
) -> V3SimulationResult:
    """Shared thermal clock; component graphs supply their physical topology.

    Internal only: a component node storage grid is not a geographic grid and
    must never construct face adjacency from its array coordinates.
    """
    if not isinstance(retain_snapshots, bool):
        raise TypeError("retain_snapshots must be a boolean")
    state = _initial_state(request)
    parameters = request.parameters
    scenario = request.scenario
    initial_fuel = state.fuel_remaining_kg.copy()
    initial_surface_water = state.heated_water_remaining_kg.copy()
    exposure = SourceIndexedExposure.from_edges(request.exposure_edges)
    cause = np.full(request.grid.shape, IGNITION_CAUSE_NONE, dtype=np.uint8)
    budget = V3EnergyBudget()
    events = sorted(
        scenario.ignitions,
        key=lambda event: (event.time_s, request.grid.cell_id(event.row, event.col)),
    )
    event_index = 0
    tolerance = 1e-10
    snapshots: list[V3Snapshot] = []
    next_sample_index = 0
    last_sample_time: float | None = None
    next_progress_time = 0.0

    def sample_time(index: int) -> float:
        if request.sample_interval_s is None:
            return np.inf
        return index * request.sample_interval_s

    def apply_events() -> None:
        nonlocal event_index
        while event_index < len(events) and events[event_index].time_s <= state.time_s + tolerance:
            event = events[event_index]
            current = state.state[event.row, event.col]
            if current in {
                int(VillageCellState.UNBURNED),
                int(VillageCellState.HEATING),
            }:
                state.state[event.row, event.col] = int(VillageCellState.BURNING)
                state.temperature_valid_mask[event.row, event.col] = False
                state.ignition_time_s[event.row, event.col] = state.time_s
                cause[event.row, event.col] = IGNITION_CAUSE_DIRECT
            elif current == int(VillageCellState.BURNING) and np.isclose(
                state.ignition_time_s[event.row, event.col],
                state.time_s,
                rtol=0.0,
                atol=tolerance,
            ):
                cause[event.row, event.col] = IGNITION_CAUSE_DIRECT
            event_index += 1

    def capture_due_sample() -> None:
        nonlocal last_sample_time, next_sample_index
        if request.sample_interval_s is None:
            return
        due = sample_time(next_sample_index)
        if state.time_s + tolerance < due:
            return
        if abs(state.time_s - due) > tolerance:
            raise RuntimeError("v3 solver stepped past an exact sample time")
        snapshot = V3Snapshot(
            time_s=due,
            state=state.state.copy(),
            surface_temperature_K=state.surface_temperature_K.copy(),
            temperature_valid_mask=state.temperature_valid_mask.copy(),
            heated_water_remaining_kg=state.heated_water_remaining_kg.copy(),
            fuel_remaining_kg=state.fuel_remaining_kg.copy(),
            interval_mean_hrr_W=state.interval_mean_hrr_W.copy(),
        )
        if snapshot_sink is not None:
            snapshot_sink.append(snapshot)
        if retain_snapshots:
            snapshots.append(snapshot)
        last_sample_time = due
        next_sample_index += 1

    while state.time_s < scenario.end_time_s - tolerance:
        if progress_callback is not None and state.time_s >= next_progress_time:
            progress_callback(state.time_s, scenario.end_time_s)
            next_progress_time = state.time_s + max(1.0, scenario.end_time_s / 100)
        apply_events()
        capture_due_sample()
        next_event = events[event_index].time_s if event_index < len(events) else np.inf
        next_sample = sample_time(next_sample_index)
        susceptible = (state.state == int(VillageCellState.UNBURNED)) | (
            state.state == int(VillageCellState.HEATING)
        )
        if np.any(susceptible):
            covered_area = parameters.covered_area_m2[susceptible]
            heated_mass_per_area = (
                parameters.effective_heated_dry_mass_kg[susceptible] / covered_area
            )
            thermal_limit = monotone_thermal_timestep_limit_s(
                state.surface_temperature_K[susceptible],
                heated_mass_per_area,
                parameters.specific_heat_J_kg_K[susceptible],
                parameters.emissivity[susceptible],
                parameters.ambient_heat_transfer_coefficient_W_m2_K[susceptible]
                + (contact.parameters.heat_transfer_coefficient_W_m2_K if contact else 0.0),
                parameters.backing_conductance_W_m2_K[susceptible],
            )
        else:
            thermal_limit = np.inf
        burning = state.state == int(VillageCellState.BURNING)
        total_duration = (
            parameters.growth_time_s + parameters.steady_time_s + parameters.decay_time_s
        )
        remaining_duration = total_duration[burning] - state.burn_age_s[burning]
        positive_duration = remaining_duration[remaining_duration > tolerance]
        burnout_limit = float(np.min(positive_duration)) if positive_duration.size else np.inf
        dt_s = min(
            scenario.max_dt_s,
            scenario.end_time_s - state.time_s,
            next_event - state.time_s,
            next_sample - state.time_s,
            thermal_limit,
            burnout_limit,
        )
        if not isfinite(dt_s) or dt_s <= tolerance:
            raise RuntimeError("v3 solver selected a non-positive time step")

        released, mass_loss, mean_hrr = burn_interval_energy_and_hrr(
            burning,
            state.burn_age_s,
            state.fuel_remaining_kg,
            parameters.peak_hrr_W,
            parameters.growth_time_s,
            parameters.steady_time_s,
            parameters.decay_time_s,
            parameters.heat_of_combustion_J_kg_dry,
            dt_s,
        )
        radiative_power = mean_hrr * parameters.radiative_fraction
        received_power = exposure.target_power_W(radiative_power.reshape(-1)).reshape(
            request.grid.shape
        )
        incident_step = received_power * dt_s
        absorbed_step = np.zeros(request.grid.shape, dtype=np.float64)
        absorbed_step[susceptible] = (
            incident_step[susceptible] * parameters.absorptivity[susceptible]
        )

        contact_transfer = (
            contact.transfer(mean_hrr, state.surface_temperature_K, susceptible)
            if contact
            else None
        )
        convective_power = (
            contact_transfer.target_power_W if contact_transfer else np.zeros(request.grid.shape)
        )
        contact_fraction = (
            contact_transfer.target_contact_fraction
            if contact_transfer
            else np.zeros(request.grid.shape)
        )
        next_temperature = state.surface_temperature_K.copy()
        next_water = state.heated_water_remaining_kg.copy()
        if np.any(susceptible):
            covered_area = parameters.covered_area_m2[susceptible]
            incident_flux = received_power[susceptible] / covered_area
            net_flux = net_surface_heat_flux_W_m2(
                state.surface_temperature_K[susceptible],
                incident_flux,
                convective_power[susceptible] / covered_area,
                parameters.absorptivity[susceptible],
                parameters.emissivity[susceptible],
                parameters.ambient_heat_transfer_coefficient_W_m2_K[susceptible]
                * (1 - contact_fraction[susceptible]),
                parameters.backing_conductance_W_m2_K[susceptible],
                scenario.ambient_temperature_K,
            )
            temperature, water_per_area = advance_surface_heating_and_evaporation(
                state.surface_temperature_K[susceptible],
                state.heated_water_remaining_kg[susceptible] / covered_area,
                parameters.effective_heated_dry_mass_kg[susceptible] / covered_area,
                parameters.specific_heat_J_kg_K[susceptible],
                net_flux,
                dt_s,
                ambient_temperature_K=scenario.ambient_temperature_K,
            )
            next_temperature[susceptible] = temperature
            next_water[susceptible] = water_per_area * covered_area

        next_fuel = np.maximum(state.fuel_remaining_kg - mass_loss, 0.0)
        next_burn_age = state.burn_age_s.copy()
        next_burn_age[burning] += dt_s
        at_curve_end = burning & (next_burn_age >= total_duration - tolerance)
        fuel_tolerance = np.maximum(1e-12, initial_fuel * 1e-9)
        if np.any(at_curve_end & (next_fuel > fuel_tolerance)):
            raise RuntimeError("v3 burn curve ended before its available fuel was consumed")
        exhausted = burning & ((next_fuel <= fuel_tolerance) | at_curve_end)
        next_fuel[exhausted] = 0.0

        next_state = state.state.copy()
        next_state[exhausted] = int(VillageCellState.BURNED_OUT)
        newly_ignited = (
            susceptible
            & request.burnable_mask
            & request.piloted_ignition_mask
            & (next_fuel > fuel_tolerance)
            & (next_temperature >= parameters.ignition_temperature_K)
        )
        next_state[newly_ignited] = int(VillageCellState.BURNING)
        remaining = susceptible & ~newly_ignited
        next_state[remaining] = int(VillageCellState.UNBURNED)
        heated = remaining & (
            next_temperature > scenario.ambient_temperature_K + scenario.heating_temperature_delta_K
        )
        next_state[heated] = int(VillageCellState.HEATING)
        next_temperature_valid = state.temperature_valid_mask.copy()
        next_temperature_valid[newly_ignited | exhausted] = False

        new_time = state.time_s + dt_s
        next_burn_age[newly_ignited] = 0.0
        state.state = next_state
        state.surface_temperature_K = next_temperature
        state.temperature_valid_mask = next_temperature_valid
        state.heated_water_remaining_kg = np.maximum(next_water, 0.0)
        state.fuel_remaining_kg = next_fuel
        state.burn_age_s = next_burn_age
        state.interval_mean_hrr_W = mean_hrr
        state.incident_radiation_J += incident_step
        state.absorbed_radiation_J += absorbed_step
        state.received_convection_J += convective_power * dt_s
        state.released_energy_J += released
        state.ignition_time_s[newly_ignited] = new_time
        cause[newly_ignited & (cause == IGNITION_CAUSE_NONE)] = IGNITION_CAUSE_THERMAL
        state.burnout_time_s[exhausted] = new_time
        state.time_s = new_time
        state.step_index += 1

        released_total = float(np.sum(released))
        radiative_emitted = float(np.sum(released * parameters.radiative_fraction))
        intercepted = float(np.sum(incident_step))
        susceptible_incident = float(np.sum(incident_step[susceptible]))
        susceptible_absorbed = float(np.sum(absorbed_step[susceptible]))
        inert = state.state == int(VillageCellState.INERT)
        inactive_combustible = request.burnable_mask & ~susceptible
        budget.released_J += released_total
        budget.radiative_emitted_J += radiative_emitted
        budget.non_radiative_released_J += released_total - radiative_emitted
        transferred = float(np.sum(convective_power)) * dt_s
        budget.convective_transferred_J += transferred
        budget.nonradiative_untransferred_J += max(
            0.0, released_total - radiative_emitted - transferred
        )
        if contact_transfer:
            budget.energy_limited_contact_source_steps += contact_transfer.energy_limited_sources
        budget.radiative_intercepted_J += intercepted
        budget.radiative_unintercepted_J += max(0.0, radiative_emitted - intercepted)
        budget.susceptible_incident_J += susceptible_incident
        budget.susceptible_absorbed_J += susceptible_absorbed
        budget.susceptible_unabsorbed_J += susceptible_incident - susceptible_absorbed
        budget.inert_received_J += float(np.sum(incident_step[inert]))
        budget.inactive_combustible_received_J += float(np.sum(incident_step[inactive_combustible]))

    apply_events()
    capture_due_sample()
    if request.sample_interval_s is not None and (
        last_sample_time is None or abs(last_sample_time - state.time_s) > tolerance
    ):
        snapshot = V3Snapshot(
            time_s=state.time_s,
            state=state.state.copy(),
            surface_temperature_K=state.surface_temperature_K.copy(),
            temperature_valid_mask=state.temperature_valid_mask.copy(),
            heated_water_remaining_kg=state.heated_water_remaining_kg.copy(),
            fuel_remaining_kg=state.fuel_remaining_kg.copy(),
            interval_mean_hrr_W=state.interval_mean_hrr_W.copy(),
        )
        if snapshot_sink is not None:
            snapshot_sink.append(snapshot)
        if retain_snapshots:
            snapshots.append(snapshot)
    if progress_callback is not None:
        progress_callback(state.time_s, scenario.end_time_s)
    return V3SimulationResult(
        final_state=state.copy(),
        initial_total_dry_fuel_kg=parameters.initial_dry_fuel_mass_kg.copy(),
        initial_fuel_kg=initial_fuel,
        initial_total_water_kg=parameters.water_mass_kg.copy(),
        initial_heated_water_kg=initial_surface_water,
        ignition_cause=cause,
        energy_budget=budget,
        snapshots=tuple(snapshots),
    )


__all__ = [
    "IGNITION_CAUSE_DIRECT",
    "IGNITION_CAUSE_NONE",
    "IGNITION_CAUSE_THERMAL",
    "V3EnergyBudget",
    "V3Scenario",
    "V3SimulationRequest",
    "V3SimulationResult",
    "V3Snapshot",
    "V3SnapshotSink",
    "V3State",
    "run_v3_simulation",
]
