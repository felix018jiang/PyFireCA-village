"""Opt-in component experiment with conserved fuel partitions and physical adjacency.

Each node owns its temperature, water, fuel, ignition and burn clock. The 1xN
kernel array is storage only; topology is constructed on the original metric
cells. Inter-cell radiation is deliberately a partition of the frozen V3 cell
exposure graph. Optional same-cell finite-surface radiation is an explicit
geometry experiment, not a calibrated crown-fire model.
"""

from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np
import yaml

from pyfireca.village.component_curves_v3 import (
    ComponentCurveConfiguration,
    apply_component_curves,
)
from pyfireca.village.component_radiation_v3 import (
    InternalRadiationGraph,
    InternalRadiationParameters,
    build_internal_radiation_graph,
    combine_internal_and_frozen_edges,
)
from pyfireca.village.component_surfaces_v3 import (
    DEFAULT_RECEIVER_SURFACE,
    ReceiverSurface,
    ReceiverSurfaceConfiguration,
)
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.finite_radiation_v3 import (
    FiniteRadiationConfiguration,
    FiniteRadiationGraph,
    build_finite_radiation_graph,
)
from pyfireca.village.flame_contact_v3 import FlameContactOperator, FlameContactParameters
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.solver_v3 import (
    V3SimulationRequest,
    V3SimulationResult,
    _run_resolved_v3_simulation,
)
from pyfireca.village.vegetation_prepare import LANDCOVER_IDS

SCHEMA = "village-components-experiment-1"
RADIATION_POLICY = "partition_existing_cell_edges_no_internal_radiation"


@dataclass(frozen=True, slots=True)
class FuelComponent:
    id: str
    live_fraction: float
    dead_fraction: float
    area_fraction: float
    footprint_fraction: float
    source_base_height_m: float
    flame_extent_m: float
    receiver_height_m: float

    def __post_init__(self) -> None:
        if not isinstance(self.id, str) or not self.id or not self.id.isascii():
            raise ValueError("component id must be a nonempty ASCII string")
        for name in tuple(self.__dataclass_fields__)[1:]:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"component {name} must be numeric")
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"component {name} must be finite and nonnegative")
        for name in ("live_fraction", "dead_fraction", "area_fraction", "footprint_fraction"):
            if getattr(self, name) > 1:
                raise ValueError(f"component {name} cannot exceed one")
        if self.live_fraction + self.dead_fraction <= 0:
            raise ValueError("a component must receive some live or dead fuel")
        for name in ("area_fraction", "footprint_fraction", "flame_extent_m", "receiver_height_m"):
            if getattr(self, name) <= 0:
                raise ValueError(f"component {name} must be positive")


@dataclass(frozen=True, slots=True)
class ComponentConfiguration:
    evidence: str
    provenance: str
    radiation_policy: str
    direct_ignition_policy: str
    recipes: dict[str, tuple[FuelComponent, ...]]

    def __post_init__(self) -> None:
        if self.evidence not in {"synthetic", "literature_informed", "measured"}:
            raise ValueError("component evidence must be explicit")
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError("component provenance must be nonempty")
        if self.radiation_policy != RADIATION_POLICY:
            raise ValueError("unsupported component radiation policy")
        if self.direct_ignition_policy != "lowest_receiver_only":
            raise ValueError("direct ignition must explicitly target the lowest receiver only")
        if not isinstance(self.recipes, dict) or not self.recipes:
            raise ValueError("component recipes must be a nonempty mapping")
        recipes = {}
        for landcover, components in self.recipes.items():
            if landcover not in LANDCOVER_IDS[1:]:
                raise ValueError(f"unsupported burnable landcover recipe: {landcover}")
            parts = tuple(components)
            if not 2 <= len(parts) <= 3 or not all(isinstance(p, FuelComponent) for p in parts):
                raise ValueError("each recipe requires two or three explicit components")
            if len({p.id for p in parts}) != len(parts):
                raise ValueError("component ids must be unique within a recipe")
            for name in ("live_fraction", "dead_fraction", "area_fraction"):
                if not np.isclose(sum(getattr(p, name) for p in parts), 1, rtol=0, atol=1e-12):
                    raise ValueError(f"component {name} partitions must sum to one")
            recipes[landcover] = parts
        object.__setattr__(self, "recipes", recipes)

    def to_mapping(self) -> dict:
        return {
            "schema_version": SCHEMA,
            "evidence": self.evidence,
            "provenance": self.provenance,
            "radiation_policy": self.radiation_policy,
            "direct_ignition_policy": self.direct_ignition_policy,
            "recipes": {
                key: [asdict(part) for part in parts] for key, parts in self.recipes.items()
            },
        }

    @classmethod
    def from_mapping(cls, value: dict) -> ComponentConfiguration:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            *cls.__dataclass_fields__,
        }:
            raise ValueError("component configuration requires exact schema keys")
        if value["schema_version"] != SCHEMA or not isinstance(value["recipes"], dict):
            raise ValueError("invalid component configuration schema or recipes")
        recipes = {}
        for key, parts in value["recipes"].items():
            if not isinstance(parts, list):
                raise ValueError("component recipe must be a list")
            recipes[key] = tuple(FuelComponent(**part) for part in parts)
        return cls(
            **{key: value[key] for key in cls.__dataclass_fields__ if key != "recipes"},
            recipes=recipes,
        )


def load_component_configuration(path: str | Path) -> ComponentConfiguration:
    return ComponentConfiguration.from_mapping(yaml.safe_load(Path(path).read_text()))


@dataclass(frozen=True, slots=True)
class ResolvedComponents:
    physical_request: V3SimulationRequest
    configuration: ComponentConfiguration
    cell_id: np.ndarray
    component_id: np.ndarray
    source_base_height_m: np.ndarray
    footprint_fraction: np.ndarray
    area_fraction: np.ndarray
    _node_request: V3SimulationRequest
    _contact: FlameContactOperator | None
    conservation: dict[str, float]
    internal_radiation_parameters: InternalRadiationParameters | None = None
    internal_radiation_graph: InternalRadiationGraph | None = None
    curve_configuration: ComponentCurveConfiguration | None = None
    curve_diagnostics: dict | None = None
    surface_configuration: ReceiverSurfaceConfiguration | None = None
    receiver_surfaces: tuple[ReceiverSurface, ...] | None = None
    finite_radiation_configuration: FiniteRadiationConfiguration | None = None
    finite_radiation_graph: FiniteRadiationGraph | None = None


def _positive_limit(name: str, value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def resolve_components(
    request: V3SimulationRequest,
    configuration: ComponentConfiguration,
    *,
    contact: FlameContactParameters | None = None,
    internal_radiation: InternalRadiationParameters | None = None,
    curves: ComponentCurveConfiguration | None = None,
    surfaces: ReceiverSurfaceConfiguration | None = None,
    finite_radiation: FiniteRadiationConfiguration | None = None,
    max_nodes: int = 20_000,
    max_radiation_edges: int = 500_000,
) -> ResolvedComponents:
    """Resolve explicit scenario hypotheses; no litter or combustible masonry is invented.

    Supplied ``contact`` is the entire contact policy; None disables it even if
    the original request contains a contact configuration, enabling ablations.
    Unknown, inert and outside cells remain one unchanged nonburnable node.
    """
    if not isinstance(request, V3SimulationRequest) or not isinstance(
        configuration, ComponentConfiguration
    ):
        raise TypeError("resolved components require a V3 request and component configuration")
    _positive_limit("max_nodes", max_nodes)
    _positive_limit("max_radiation_edges", max_radiation_edges)
    if finite_radiation is not None:
        if not isinstance(finite_radiation, FiniteRadiationConfiguration):
            raise TypeError("finite_radiation must be FiniteRadiationConfiguration or None")
        if internal_radiation is not None:
            raise ValueError(
                "complete finite radiation conflicts with frozen-plus-internal radiation"
            )
    if surfaces is not None:
        if not isinstance(surfaces, ReceiverSurfaceConfiguration):
            raise TypeError("surfaces must be ReceiverSurfaceConfiguration or None")
        if internal_radiation is None and finite_radiation is None:
            raise ValueError(
                "receiver surfaces require internal radiation or complete finite radiation; "
                "configuration cannot be ignored"
            )
        surfaces.validate_component_ids(
            {
                label: tuple(part.id for part in parts)
                for label, parts in configuration.recipes.items()
            }
        )
    if curves is not None:
        if not isinstance(curves, ComponentCurveConfiguration):
            raise TypeError("curves must be ComponentCurveConfiguration or None")
        curves.validate_component_ids(
            {
                label: tuple(part.id for part in parts)
                for label, parts in configuration.recipes.items()
            }
        )
    if internal_radiation is not None and not isinstance(
        internal_radiation, InternalRadiationParameters
    ):
        raise TypeError("internal_radiation must be InternalRadiationParameters or None")
    if contact is not None:
        if not isinstance(contact, FlameContactParameters):
            raise TypeError("contact must be FlameContactParameters or None")
        if contact.receiver_height_overrides_m:
            raise ValueError(
                "material contact height overrides require the single-state solver; "
                "component recipes define their own receiver heights"
            )
        if contact.receiver_vertical_half_extent_m > 0:
            raise ValueError("finite receiver bands require the single-state solver")
        if contact.terrain_reference != "cell_center":
            raise ValueError("shared-face terrain contact requires the single-state solver")
        if request.grid.cell_size_m > contact.max_cell_size_m + 1e-12:
            raise ValueError("component contact grid exceeds its explicit maximum cell size")
    count = request.grid.height * request.grid.width
    burnable = request.burnable_mask.ravel()
    landcover = request.landcover_index.ravel()
    if np.any(landcover[burnable] >= len(LANDCOVER_IDS)):
        raise ValueError("component recipes require the standard V3 landcover index contract")
    physics = request.parameters
    recipes = []
    node_count = 0
    for cell in range(count):
        label = (
            LANDCOVER_IDS[int(landcover[cell])]
            if burnable[cell] and 0 <= landcover[cell] < len(LANDCOVER_IDS)
            else None
        )
        parts = configuration.recipes.get(label) if label is not None else None
        recipes.append(parts)
        node_count += len(parts) if parts else 1
    if node_count > max_nodes:
        raise ValueError(
            f"component node limit exceeded before allocation: {node_count} > {max_nodes}"
        )
    counts = np.asarray([len(parts) if parts else 1 for parts in recipes], dtype=np.int32)
    edge_count = int(
        np.sum(
            counts[request.exposure_edges.source].astype(np.int64)
            * counts[request.exposure_edges.target]
        )
    )
    if finite_radiation is not None:
        edge_count = 0  # Its own candidate/visibility limits are checked before quadrature.
    if internal_radiation is not None:
        part_counts = counts[burnable].astype(np.int64)
        edge_count += int(np.sum(part_counts * (part_counts - 1)))
    if edge_count > max_radiation_edges:
        raise ValueError(f"component radiation edge limit exceeded before allocation: {edge_count}")
    cell_ids = np.repeat(np.arange(count), counts)
    fields = {
        key: getattr(physics, key).ravel()[cell_ids].copy() for key in physics.__dataclass_fields__
    }
    names, bases, footprints, areas = [], [], [], []
    nodes_by_cell = []
    cursor = 0
    for cell, parts in enumerate(recipes):
        ids = np.arange(cursor, cursor + counts[cell], dtype=np.int32)
        nodes_by_cell.append(ids)
        cursor += counts[cell]
        if parts is None:
            names.append("whole")
            bases.append(0.0)
            footprints.append(1.0)
            areas.append(1.0)
            continue
        dry = physics.initial_dry_fuel_mass_kg.ravel()[cell]
        heated_ratio = physics.effective_heated_dry_mass_kg.ravel()[cell] / dry
        for node, part in zip(ids, parts, strict=True):
            names.append(part.id)
            bases.append(part.source_base_height_m)
            footprints.append(part.footprint_fraction)
            areas.append(part.area_fraction)
            live = physics.live_dry_fuel_mass_kg.ravel()[cell] * part.live_fraction
            dead = physics.dead_dry_fuel_mass_kg.ravel()[cell] * part.dead_fraction
            fraction = (live + dead) / dry
            if fraction <= 0:
                raise ValueError(f"cell {cell} component {part.id} has zero resolved dry fuel")
            for key in (
                "initial_dry_fuel_mass_kg",
                "pyrolysable_dry_fuel_mass_kg",
                "peak_hrr_W",
                "active_surface_area_m2",
                "available_energy_J",
                "configured_curve_energy_J",
            ):
                fields[key][node] *= fraction
            fields["live_dry_fuel_mass_kg"][node] = live
            fields["dead_dry_fuel_mass_kg"][node] = dead
            fields["live_water_mass_kg"][node] *= part.live_fraction
            fields["dead_water_mass_kg"][node] *= part.dead_fraction
            water = fields["live_water_mass_kg"][node] + fields["dead_water_mass_kg"][node]
            fields["water_mass_kg"][node] = water
            fields["moisture_content_dry_basis"][node] = water / (live + dead)
            fields["effective_heated_dry_mass_kg"][node] = heated_ratio * (live + dead)
            for key in ("covered_area_m2", "coverage_fraction", "receiver_area_m2"):
                fields[key][node] *= part.area_fraction
            covered = fields["covered_area_m2"][node]
            fields["receiver_area_ratio"][node] = fields["receiver_area_m2"][node] / covered
            fields["active_surface_area_ratio"][node] = (
                fields["active_surface_area_m2"][node] / covered
            )
            fields["source_height_m"][node] = part.flame_extent_m
            fields["receiver_height_m"][node] = part.receiver_height_m
    areas = np.asarray(areas, dtype=np.float64)
    curve_diagnostics = None
    if curves is not None:
        curve_diagnostics = apply_component_curves(
            fields, cell_ids, names, landcover, burnable, curves
        )
        restored_peak = np.bincount(cell_ids, weights=fields["peak_hrr_W"], minlength=count)
        curve_diagnostics["physical_cell_peak_sum_max_abs_change_W"] = float(
            np.max(np.abs(restored_peak - physics.peak_hrr_W.ravel()))
        )
        curve_diagnostics["peak_sums_are_not_simultaneous_realized_cell_peaks"] = True
    node_physics = V3CellPhysicsArrays(
        **{key: value.reshape(1, -1) for key, value in fields.items()}
    )
    conservation = {}
    for key in (
        "initial_dry_fuel_mass_kg",
        "live_dry_fuel_mass_kg",
        "dead_dry_fuel_mass_kg",
        "water_mass_kg",
        "live_water_mass_kg",
        "dead_water_mass_kg",
        "pyrolysable_dry_fuel_mass_kg",
        "effective_heated_dry_mass_kg",
        "available_energy_J",
        "configured_curve_energy_J",
        "peak_hrr_W",
        "active_surface_area_m2",
        "covered_area_m2",
        "receiver_area_m2",
    ):
        if key == "peak_hrr_W" and curves is not None:
            continue  # Peak power changes with the clock; integrated fuel energy must still close.
        restored = np.bincount(cell_ids, weights=fields[key], minlength=count)
        expected = getattr(physics, key).ravel()
        if not np.allclose(restored, expected, rtol=1e-10, atol=1e-6):
            raise ValueError(f"component partition does not conserve {key}")
        conservation[key] = float(np.max(np.abs(restored - expected)))
    heated_water = fields["effective_heated_dry_mass_kg"] * fields["moisture_content_dry_basis"]
    restored = np.bincount(cell_ids, weights=heated_water, minlength=count)
    expected = (physics.effective_heated_dry_mass_kg * physics.moisture_content_dry_basis).ravel()
    if not np.allclose(restored, expected, rtol=1e-10, atol=1e-9):
        raise ValueError("component partition does not conserve heated water")
    conservation["heated_water_kg"] = float(np.max(np.abs(restored - expected)))

    # Each source component owns only its own HRR. Receiver partitions share
    # every original outgoing cell weight, so this lift cannot duplicate energy.
    sources, targets, weights = [], [], []
    for source, target, weight in zip(
        request.exposure_edges.source if finite_radiation is None else (),
        request.exposure_edges.target if finite_radiation is None else (),
        request.exposure_edges.weight if finite_radiation is None else (),
        strict=True,
    ):
        for snode in nodes_by_cell[source]:
            for tnode in nodes_by_cell[target]:
                sources.append(snode)
                targets.append(tnode)
                weights.append(weight * areas[tnode])
    edges = ExposureEdges(
        np.asarray(sources, dtype=np.int32),
        np.asarray(targets, dtype=np.int32),
        np.asarray(weights),
        node_count,
    )
    internal_graph = None
    finite_graph = None
    receiver_surfaces = None
    if surfaces is not None:
        selected_surfaces = []
        for node, cell in enumerate(cell_ids):
            selected = DEFAULT_RECEIVER_SURFACE
            if burnable[cell]:
                label = LANDCOVER_IDS[int(landcover[cell])]
                explicit = surfaces.surfaces.get(label, {}).get(names[node])
                if explicit is not None:
                    explicit.validate_bounds(
                        fields["receiver_area_m2"][node],
                        fields["receiver_height_m"][node],
                        request.grid.cell_size_m,
                    )
                    selected = explicit
            selected_surfaces.append(selected)
        receiver_surfaces = tuple(selected_surfaces)
    if finite_radiation is not None:
        finite_graph = build_finite_radiation_graph(
            request.grid,
            cell_ids,
            burnable[cell_ids],
            request.source_mask.ravel()[cell_ids],
            request.target_mask.ravel()[cell_ids],
            node_physics,
            np.asarray(bases),
            np.asarray(footprints),
            physics.coverage_fraction.ravel()[cell_ids],
            finite_radiation,
            receiver_surfaces=receiver_surfaces,
            ground_elevation_m=request.ground_elevation_m,
            domain_mask=request.domain_mask,
            known_mask=request.known_mask,
            max_edges=max_radiation_edges,
        )
        edges = finite_graph.edges
    if internal_radiation is not None:
        internal_graph = build_internal_radiation_graph(
            request.grid,
            cell_ids,
            burnable[cell_ids],
            node_physics,
            np.asarray(bases),
            np.asarray(footprints),
            physics.coverage_fraction.ravel()[cell_ids],
            internal_radiation,
            receiver_surfaces,
        )
        edges = combine_internal_and_frozen_edges(edges, internal_graph.edges)
    events = []
    for event in request.scenario.ignitions:
        cell = request.grid.cell_id(event.row, event.col)
        ids = nodes_by_cell[cell]
        node = int(ids[np.argmin(fields["receiver_height_m"][ids])])
        events.append(VillageIgnitionEvent(event.time_s, 0, node, event.kind))
    node_grid = VillageGridSpec(
        1, node_count, request.grid.cell_size_m, 0, request.grid.cell_size_m
    )
    kwargs = {
        key: getattr(request, key).ravel()[cell_ids].reshape(1, -1)
        for key in (
            "domain_mask",
            "known_mask",
            "burnable_mask",
            "source_mask",
            "target_mask",
            "piloted_ignition_mask",
            "material_index",
            "landcover_index",
            "building_index",
        )
    }
    node_request = V3SimulationRequest(
        grid=node_grid,
        parameters=node_physics,
        exposure_edges=edges,
        scenario=replace(request.scenario, ignitions=tuple(events)),
        unknown_policy=request.unknown_policy,
        sample_interval_s=request.sample_interval_s,
        **kwargs,
    )
    bases = np.asarray(bases)
    footprints = np.asarray(footprints)
    operator = None
    if contact is not None:
        neighbors = np.full((node_count, 15), -1, dtype=np.int32)
        geometry = np.zeros((node_count, 15))
        ground = (
            np.zeros(count)
            if request.ground_elevation_m is None
            else request.ground_elevation_m.ravel()
        )
        for cell in np.flatnonzero(burnable):
            row, col = request.grid.row_col(int(cell))
            candidates = [int(cell)]
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                rr, cc = row + dr, col + dc
                if 0 <= rr < request.grid.height and 0 <= cc < request.grid.width:
                    target = request.grid.cell_id(rr, cc)
                    if burnable[target]:
                        candidates.append(target)
            for snode in nodes_by_cell[cell]:
                bottom = ground[cell] + bases[snode]
                top = bottom + fields["source_height_m"][snode]
                slot = 0
                for target in candidates:
                    for tnode in nodes_by_cell[target]:
                        z = ground[target] + fields["receiver_height_m"][tnode]
                        if snode != tnode and bottom <= z <= top:
                            neighbors[snode, slot] = tnode
                            geometry[snode, slot] = (
                                physics.coverage_fraction.ravel()[cell] * footprints[snode]
                            )
                            slot += 1
        neighbors.setflags(write=False)
        geometry.setflags(write=False)
        operator = FlameContactOperator(neighbors, geometry, contact, node_physics)
    return ResolvedComponents(
        request,
        configuration,
        cell_ids,
        np.asarray(names),
        bases,
        footprints,
        areas,
        node_request,
        operator,
        conservation,
        internal_radiation,
        internal_graph,
        curves,
        curve_diagnostics,
        surfaces,
        receiver_surfaces,
        finite_radiation,
        finite_graph,
    )


def run_component_simulation(
    resolved: ResolvedComponents, *, retain_snapshots: bool = False
) -> V3SimulationResult:
    if not isinstance(resolved, ResolvedComponents):
        raise TypeError("resolved must be ResolvedComponents")
    return _run_resolved_v3_simulation(
        resolved._node_request, contact=resolved._contact, retain_snapshots=retain_snapshots
    )


def component_summary(resolved: ResolvedComponents, result: V3SimulationResult) -> dict:
    """A component fire is not evidence of whole-building loss or crown transition."""
    ignited = np.isfinite(result.final_state.ignition_time_s.ravel())
    summary = {}
    landcover = resolved.physical_request.landcover_index.ravel()[resolved.cell_id]
    for label in np.unique(resolved.component_id):
        for index in np.unique(landcover[resolved.component_id == label]):
            mask = (resolved.component_id == label) & (landcover == index)
            name = (
                LANDCOVER_IDS[int(index)] if 0 <= index < len(LANDCOVER_IDS) else f"index_{index}"
            )
            summary[f"{name}/{label}"] = {
                "nodes": int(mask.sum()),
                "ignited": int(ignited[mask].sum()),
            }
    return summary


def write_component_run(
    resolved: ResolvedComponents,
    result: V3SimulationResult,
    output: str | Path,
    *,
    material_labels: dict[int, str] | None = None,
    landcover_labels: dict[int, str] | None = None,
    scenario_metadata: dict | None = None,
) -> Path:
    """Write independent component and physical-cell diagnostics with a restorable base case."""
    if result.final_state.state.shape != resolved._node_request.grid.shape or not np.array_equal(
        result.initial_fuel_kg,
        np.where(
            resolved._node_request.burnable_mask,
            resolved._node_request.parameters.pyrolysable_dry_fuel_mass_kg,
            0,
        ),
    ):
        raise ValueError("component result does not match the resolved node request")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    request = resolved.physical_request
    fields = {
        key: getattr(result.final_state, key).ravel()
        for key in (
            "state",
            "surface_temperature_K",
            "temperature_valid_mask",
            "heated_water_remaining_kg",
            "fuel_remaining_kg",
            "ignition_time_s",
            "burnout_time_s",
            "released_energy_J",
            "incident_radiation_J",
            "absorbed_radiation_J",
            "received_convection_J",
        )
    }
    np.savez(
        output / "components.final.npz",
        cell_id=resolved.cell_id,
        component_id=resolved.component_id,
        **fields,
    )
    initial = result.initial_fuel_kg.ravel()
    consumed = np.bincount(
        resolved.cell_id,
        weights=initial - fields["fuel_remaining_kg"],
        minlength=request.domain_mask.size,
    )
    parent_fuel = request.parameters.pyrolysable_dry_fuel_mass_kg.ravel()
    fraction = np.divide(consumed, parent_fuel, out=np.zeros_like(consumed), where=parent_fuel > 0)
    any_ignited = (
        np.bincount(
            resolved.cell_id,
            weights=np.isfinite(fields["ignition_time_s"]),
            minlength=request.domain_mask.size,
        )
        > 0
    )
    np.savez(
        output / "physical_cells.final.npz",
        any_component_ignited=any_ignited.reshape(request.grid.shape),
        consumed_fuel_fraction=fraction.reshape(request.grid.shape),
        domain_mask=request.domain_mask,
        known_mask=request.known_mask,
        burnable_mask=request.burnable_mask,
    )
    table = []
    for node, cell in enumerate(resolved.cell_id):
        row, col = request.grid.row_col(int(cell))
        table.append(
            {
                "node_id": node,
                "cell_id": int(cell),
                "row": row,
                "col": col,
                "component_id": str(resolved.component_id[node]),
                "source_base_height_m": float(resolved.source_base_height_m[node]),
                "flame_extent_m": float(
                    resolved._node_request.parameters.source_height_m.ravel()[node]
                ),
                "receiver_height_m": float(
                    resolved._node_request.parameters.receiver_height_m.ravel()[node]
                ),
                "initial_pyrolysable_fuel_kg": float(initial[node]),
                **{
                    key: float(getattr(resolved._node_request.parameters, key).ravel()[node])
                    for key in (
                        "growth_time_s",
                        "steady_time_s",
                        "decay_time_s",
                        "peak_hrr_W",
                        "peak_hrrpua_W_m2_active",
                        "active_surface_area_m2",
                        "available_energy_J",
                        "configured_curve_energy_J",
                    )
                },
                "burnout_time_s": ""
                if not np.isfinite(fields["burnout_time_s"][node])
                else float(fields["burnout_time_s"][node]),
                "ignition_time_s": ""
                if not np.isfinite(fields["ignition_time_s"][node])
                else float(fields["ignition_time_s"][node]),
            }
        )
    with (output / "components.csv").open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(table[0]))
        writer.writeheader()
        writer.writerows(table)
    from pyfireca.village.workflow_v3 import write_v3_case_bundle

    resolved_contact = resolved._contact.parameters if resolved._contact else None
    write_v3_case_bundle(
        replace(request, flame_contact=resolved_contact),
        output / "base-case",
        material_labels=material_labels,
        landcover_labels=landcover_labels,
        metadata=scenario_metadata,
    )
    if resolved_contact is not None:
        (output / "flame-contact.resolved.yml").write_text(
            yaml.safe_dump(resolved_contact.to_mapping(), sort_keys=False)
        )
    config = resolved.configuration.to_mapping()
    (output / "components.resolved.yml").write_text(yaml.safe_dump(config, sort_keys=False))
    if resolved.curve_configuration is not None:
        (output / "component-curves.resolved.yml").write_text(
            yaml.safe_dump(resolved.curve_configuration.to_mapping(), sort_keys=False)
        )
    if resolved.surface_configuration is not None:
        (output / "receiver-surfaces.resolved.yml").write_text(
            yaml.safe_dump(resolved.surface_configuration.to_mapping(), sort_keys=False)
        )
        surface_table = []
        for node, surface in enumerate(resolved.receiver_surfaces):
            geometry = (
                surface.geometry(
                    resolved._node_request.parameters.receiver_area_m2.ravel()[node],
                    resolved._node_request.parameters.receiver_height_m.ravel()[node],
                )
                if resolved._node_request.burnable_mask.ravel()[node]
                else None
            )
            surface_table.append(
                {
                    "node_id": node,
                    "cell_id": int(resolved.cell_id[node]),
                    "component_id": str(resolved.component_id[node]),
                    **asdict(surface),
                    "width_m": geometry[4] if geometry else "",
                    "height_m": geometry[5] if geometry else "",
                    "normal_x": geometry[3][0] if geometry else "",
                    "normal_y": geometry[3][1] if geometry else "",
                    "normal_z": geometry[3][2] if geometry else "",
                }
            )
        with (output / "receiver-surfaces.csv").open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(surface_table[0]))
            writer.writeheader()
            writer.writerows(surface_table)
    if resolved.internal_radiation_parameters is not None:
        (output / "internal-radiation.resolved.yml").write_text(
            yaml.safe_dump(resolved.internal_radiation_parameters.to_mapping(), sort_keys=False)
        )
        graph = resolved.internal_radiation_graph
        radiative_energy = (
            result.final_state.released_energy_J
            * resolved._node_request.parameters.radiative_fraction
        ).ravel()
        internal_incident = np.bincount(
            graph.edges.target,
            weights=radiative_energy[graph.edges.source] * graph.edges.weight,
            minlength=len(resolved.cell_id),
        )
        np.savez(
            output / "internal_radiation.graph.npz",
            source=graph.edges.source,
            target=graph.edges.target,
            weight=graph.edges.weight,
            source_radius_m=graph.source_radius_m,
            source_surface_area_m2=graph.source_surface_area_m2,
            quadrature_used_order=graph.quadrature_used_order,
            estimated_absolute_error=graph.estimated_absolute_error,
            internal_incident_radiation_J=internal_incident,
        )
    if resolved.finite_radiation_configuration is not None:
        (output / "finite-radiation.resolved.yml").write_text(
            yaml.safe_dump(resolved.finite_radiation_configuration.to_mapping(), sort_keys=False)
        )
        graph = resolved.finite_radiation_graph
        radiative = (
            result.final_state.released_energy_J
            * resolved._node_request.parameters.radiative_fraction
        ).ravel()
        incident = np.bincount(
            graph.edges.target,
            weights=radiative[graph.edges.source] * graph.edges.weight,
            minlength=len(resolved.cell_id),
        )
        np.savez(
            output / "finite_radiation.graph.npz",
            source=graph.edges.source,
            target=graph.edges.target,
            weight=graph.edges.weight,
            source_radius_m=graph.source_radius_m,
            source_surface_area_m2=graph.source_surface_area_m2,
            quadrature_used_order=graph.quadrature_used_order,
            estimated_absolute_error=graph.estimated_absolute_error,
            total_incident_radiation_J=incident,
        )
    manifest = {
        "schema_version": "village-components-run-1",
        "physical_grid": asdict(request.grid),
        "component_configuration": config,
        "component_curves": resolved.curve_configuration.to_mapping()
        if resolved.curve_configuration
        else None,
        "component_curve_diagnostics": resolved.curve_diagnostics,
        "receiver_surfaces": (
            resolved.surface_configuration.to_mapping() if resolved.surface_configuration else None
        ),
        "receiver_geometry_affects": (
            "all_component_radiation"
            if resolved.finite_radiation_graph
            else "same_cell_radiation_only"
        ),
        "finite_radiation": (
            resolved.finite_radiation_configuration.to_mapping()
            if resolved.finite_radiation_configuration
            else None
        ),
        "finite_radiation_diagnostics": (
            resolved.finite_radiation_graph.summary() if resolved.finite_radiation_graph else None
        ),
        "resolved_radiation_policy": (
            "complete_finite_surfaces_with_explicit_visibility"
            if resolved.finite_radiation_graph
            else "frozen_cell_edges_plus_optional_internal_surfaces"
        ),
        "internal_radiation": (
            resolved.internal_radiation_parameters.to_mapping()
            if resolved.internal_radiation_parameters
            else None
        ),
        "internal_radiation_diagnostics": (
            resolved.internal_radiation_graph.summary()
            if resolved.internal_radiation_graph
            else None
        ),
        "flame_contact": resolved._contact.parameters.to_mapping() if resolved._contact else None,
        "scenario": {
            **asdict(request.scenario),
            "ignitions": [asdict(e) for e in request.scenario.ignitions],
        },
        "resolved_direct_ignitions": [asdict(e) for e in resolved._node_request.scenario.ignitions],
        "summary": component_summary(resolved, result),
        "energy_budget": asdict(result.energy_budget),
        "conservation_max_abs_error": resolved.conservation,
        "node_count": len(resolved.cell_id),
        "radiation_edge_count": len(resolved._node_request.exposure_edges.source),
        "requires_independent_calibration": True,
        "limitations": [
            "synthetic partitions are hypotheses, not survey measurements",
            (
                "finite radiation replaces the inherited graph; static opaque receivers, "
                "stepwise terrain and center-distance truncation are scenario hypotheses"
                if resolved.finite_radiation_graph
                else "intercell radiation preserves the original cell-level graph; "
                "internal finite-surface geometry remains an explicit scenario proxy"
                if resolved.internal_radiation_graph
                else "radiation preserves cell-level visibility and geometry; no internal radiation"
            ),
            (
                "explicit prescribed component HRR clocks conserve fuel energy; "
                "peak ceilings are scenario assumptions, not calibrated material limits"
                if resolved.curve_configuration
                else "inherited HRR curve shapes"
            ),
            "inherited thermal properties; no field crown-transition validation",
            "static flame geometry does not respond to revised HRR; prescribed flaming curves "
            "do not resolve moisture-dependent burning, oxygen limitation or smouldering",
            "no embers, wind-dependent flame geometry, enclosure or whole-building loss model",
            "physical-cell any_component_ignited does not mean "
            "whole-cell consumption or building destruction",
        ],
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    project_root = Path(__file__).resolve().parents[3]
    tracked = [
        *project_root.joinpath("src/pyfireca/village").glob("*.py"),
        *project_root.joinpath("src/pyfireca/village/web").glob("*.js"),
        *project_root.joinpath("src/pyfireca/village/web").glob("*.mjs"),
        *project_root.joinpath("src/pyfireca/village/web").glob("*.html"),
        *project_root.joinpath("src/pyfireca/village/web").glob("*.css"),
        project_root / "pyproject.toml",
    ]
    source_dir = output / "source_snapshot"
    hashes = {}
    for source in tracked:
        if source.exists():
            data = source.read_bytes()
            relative = source.relative_to(project_root)
            target = source_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            hashes[str(relative)] = hashlib.sha256(data).hexdigest()
    (output / "source_sha256.json").write_text(json.dumps(hashes, indent=2) + "\n")
    return output
