"""Height-aware, resource-bounded radiative transport for vegetation model v3.

The module constructs one directed graph shared by building and vegetation
cells.  Source flame height and target receiver height are resolved per cell.
It deliberately contains no ignition or state-transition logic; those belong
to the V05 solver.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import asdict, dataclass
from math import atan, cos, exp, pi, radians, sin
from pathlib import Path

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.physics_v3 import ResolvedV3Physics
from pyfireca.village.transport_v2 import _line_cells
from pyfireca.village.vegetation_config import VegetationCase

DEFAULT_MAX_V3_TRANSPORT_EDGES = 5_000_000
BYTES_PER_FINAL_EDGE = 16
CONSERVATIVE_BUILD_BYTES_PER_CANDIDATE_EDGE = 64
REPLAY_BYTES_PER_CELL_FRAME = 33


@dataclass(frozen=True, slots=True)
class V3RadiativeTransportParameters:
    """Global geometry inputs; source and receiver heights remain per-cell."""

    exposure_radius_m: float
    near_field_regularization_m: float
    atmospheric_extinction_m_inv: float
    wind_speed_m_s: float
    wind_from_deg: float
    flame_tilt_coefficient_s_m: float

    def __post_init__(self) -> None:
        numeric = tuple(getattr(self, name) for name in self.__dataclass_fields__)
        if not all(np.isfinite(value) for value in numeric):
            raise ValueError("v3 transport parameters must be finite")
        if self.exposure_radius_m <= 0.0:
            raise ValueError("v3 exposure radius must be positive")
        if self.near_field_regularization_m < 0.0:
            raise ValueError("v3 near-field regularization must be non-negative")
        if self.atmospheric_extinction_m_inv < 0.0:
            raise ValueError("v3 atmospheric extinction must be non-negative")
        if self.wind_speed_m_s < 0.0 or self.flame_tilt_coefficient_s_m < 0.0:
            raise ValueError("v3 wind speed and flame tilt coefficient must be non-negative")
        if not 0.0 <= self.wind_from_deg < 360.0:
            raise ValueError("v3 wind_from_deg must lie in [0, 360)")


@dataclass(frozen=True, slots=True)
class V3TransportResourceEstimate:
    """Conservative storage estimate made before graph allocation."""

    cell_count: int
    source_cell_count: int
    target_cell_count: int
    offset_count: int
    candidate_edge_upper_bound: int
    final_edge_bytes_upper_bound: int
    construction_peak_bytes_estimate: int
    replay_frame_count: int
    replay_resident_bytes_estimate: int
    construction_plus_replay_bytes_estimate: int

    def as_dict(self) -> dict[str, int | float]:
        values: dict[str, int | float] = asdict(self)
        for key in (
            "final_edge_bytes_upper_bound",
            "construction_peak_bytes_estimate",
            "replay_resident_bytes_estimate",
            "construction_plus_replay_bytes_estimate",
        ):
            values[f"{key.removesuffix('_estimate').removesuffix('_upper_bound')}_MB"] = (
                values[key] / 1_000_000
            )
        return values


def parameters_from_case(case: VegetationCase) -> V3RadiativeTransportParameters:
    """Resolve the evidenced V02 transport and weather values without defaults."""

    if not isinstance(case, VegetationCase):
        raise TypeError("case must be a VegetationCase")
    return V3RadiativeTransportParameters(
        exposure_radius_m=case.transport["exposure_radius_m"].value,
        near_field_regularization_m=case.transport["near_field_regularization_m"].value,
        atmospheric_extinction_m_inv=case.transport["atmospheric_extinction_m_inv"].value,
        wind_speed_m_s=case.weather["wind_speed_m_s"].value,
        wind_from_deg=case.weather["wind_from_deg"].value,
        flame_tilt_coefficient_s_m=case.transport["flame_tilt_coefficient_s_m"].value,
    )


def _offsets(
    grid: VillageGridSpec, parameters: V3RadiativeTransportParameters
) -> tuple[tuple[int, int, tuple[tuple[int, int], ...]], ...]:
    max_offset = int(np.floor(parameters.exposure_radius_m / grid.cell_size_m))
    values = []
    for drow in range(-max_offset, max_offset + 1):
        for dcol in range(-max_offset, max_offset + 1):
            if drow == 0 and dcol == 0:
                continue
            # Oversized offsets have no source/target pair. Negative slice
            # stops otherwise wrap in Python and can create incompatible
            # arrays or ghost pairs on small/cropped domains.
            if abs(drow) >= grid.height or abs(dcol) >= grid.width:
                continue
            if np.hypot(drow, dcol) * grid.cell_size_m > parameters.exposure_radius_m + 1e-12:
                continue
            path = _line_cells(0, 0, drow, dcol)[1:-1]
            values.append((drow, dcol, path))
    return tuple(values)


def _pair_slices(grid: VillageGridSpec, drow: int, dcol: int) -> tuple[slice, slice, slice, slice]:
    source_row_start = max(0, -drow)
    source_row_stop = min(grid.height, grid.height - drow)
    source_col_start = max(0, -dcol)
    source_col_stop = min(grid.width, grid.width - dcol)
    return (
        slice(source_row_start, source_row_stop),
        slice(source_col_start, source_col_stop),
        slice(source_row_start + drow, source_row_stop + drow),
        slice(source_col_start + dcol, source_col_stop + dcol),
    )


def _validated_mask(name: str, value: NDArray[np.bool_], shape: tuple[int, int]) -> NDArray:
    array = np.asarray(value)
    if array.shape != shape or array.dtype != np.bool_:
        raise TypeError(f"{name} must be a boolean array matching the grid")
    return array


def _validated_float_array(
    name: str, value: NDArray[np.floating], shape: tuple[int, int]
) -> NDArray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape:
        raise ValueError(f"{name} must match the grid")
    if np.any(~np.isfinite(array)) or np.any(array < 0.0):
        raise ValueError(f"{name} must be finite and non-negative")
    return array


def _candidate_edge_count(
    grid: VillageGridSpec,
    source: NDArray[np.bool_],
    target: NDArray[np.bool_],
    offsets: tuple[tuple[int, int, tuple[tuple[int, int], ...]], ...],
) -> int:
    total = 0
    for drow, dcol, _ in offsets:
        source_rows, source_cols, target_rows, target_cols = _pair_slices(grid, drow, dcol)
        total += int(
            np.count_nonzero(source[source_rows, source_cols] & target[target_rows, target_cols])
        )
    return total


def estimate_v3_transport_resources(
    grid: VillageGridSpec,
    source_mask: NDArray[np.bool_],
    target_mask: NDArray[np.bool_],
    parameters: V3RadiativeTransportParameters,
    *,
    replay_frame_count: int = 0,
) -> V3TransportResourceEstimate:
    """Count all in-radius source/target pairs and estimate peak storage.

    The candidate count is exact before path transmissivity removes zero-weight
    edges, so it is a safe graph-size upper bound without assuming blockers.
    Replay storage follows the V04 planning basis: four float64 fields and one
    uint8 state value per cell and frame.
    """

    source = _validated_mask("source_mask", source_mask, grid.shape)
    target = _validated_mask("target_mask", target_mask, grid.shape)
    if isinstance(replay_frame_count, bool) or not isinstance(replay_frame_count, int):
        raise TypeError("replay_frame_count must be an integer")
    if replay_frame_count < 0:
        raise ValueError("replay_frame_count must be non-negative")
    offsets = _offsets(grid, parameters)
    candidate_count = _candidate_edge_count(grid, source, target, offsets)
    final_bytes = candidate_count * BYTES_PER_FINAL_EDGE
    construction_bytes = candidate_count * CONSERVATIVE_BUILD_BYTES_PER_CANDIDATE_EDGE
    replay_bytes = grid.height * grid.width * replay_frame_count * REPLAY_BYTES_PER_CELL_FRAME
    return V3TransportResourceEstimate(
        cell_count=grid.height * grid.width,
        source_cell_count=int(np.count_nonzero(source)),
        target_cell_count=int(np.count_nonzero(target)),
        offset_count=len(offsets),
        candidate_edge_upper_bound=candidate_count,
        final_edge_bytes_upper_bound=final_bytes,
        construction_peak_bytes_estimate=construction_bytes,
        replay_frame_count=replay_frame_count,
        replay_resident_bytes_estimate=replay_bytes,
        construction_plus_replay_bytes_estimate=construction_bytes + replay_bytes,
    )


def enforce_v3_transport_resource_limits(
    estimate: V3TransportResourceEstimate,
    *,
    max_edge_count: int,
    max_memory_mb: int,
) -> None:
    """Fail before graph construction when configured resource limits are exceeded."""

    for name, value in (("max_edge_count", max_edge_count), ("max_memory_mb", max_memory_mb)):
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise ValueError(f"{name} must be a positive integer")
    if estimate.candidate_edge_upper_bound > max_edge_count:
        raise ValueError(
            "RESOURCE_LIMIT: v3 candidate edge upper bound "
            f"{estimate.candidate_edge_upper_bound:,} exceeds max_edge_count "
            f"{max_edge_count:,}; no edges were built. Explicit options are to validate "
            "a smaller physical exposure radius, prepare a separately documented coarser "
            "grid, or raise the limit only after checking the reported memory estimate."
        )
    peak_mb = estimate.construction_peak_bytes_estimate / 1_000_000
    if peak_mb > max_memory_mb:
        raise ValueError(
            "RESOURCE_LIMIT: v3 graph construction peak estimate "
            f"{peak_mb:.1f} MB exceeds max_memory_mb {max_memory_mb}; no edges were built. "
            "Use streamed/block construction in a larger validated budget or revise an "
            "explicit physical/grid scenario parameter."
        )


def _validate_transport_arrays(
    grid: VillageGridSpec,
    source_mask: NDArray[np.bool_],
    target_mask: NDArray[np.bool_],
    receiver_area_m2: NDArray[np.floating],
    source_height_m: NDArray[np.floating],
    receiver_height_m: NDArray[np.floating],
    blocker_transmissivity: NDArray[np.floating],
) -> tuple[NDArray, NDArray, NDArray, NDArray, NDArray, NDArray]:
    source = _validated_mask("source_mask", source_mask, grid.shape)
    target = _validated_mask("target_mask", target_mask, grid.shape)
    area = _validated_float_array("receiver_area_m2", receiver_area_m2, grid.shape)
    source_height = _validated_float_array("source_height_m", source_height_m, grid.shape)
    receiver_height = _validated_float_array("receiver_height_m", receiver_height_m, grid.shape)
    transmission = _validated_float_array(
        "blocker_transmissivity", blocker_transmissivity, grid.shape
    )
    if np.any(transmission > 1.0):
        raise ValueError("blocker_transmissivity must lie in [0, 1]")
    if np.any(source & (source_height <= 0.0)):
        raise ValueError("every v3 transport source requires positive source height")
    if np.any(target & (area <= 0.0)):
        raise ValueError("every v3 transport target requires positive receiver area")
    return source, target, area, source_height, receiver_height, transmission


def _wind_geometry(parameters: V3RadiativeTransportParameters) -> tuple[float, float, float]:
    tilt_angle = atan(parameters.flame_tilt_coefficient_s_m * parameters.wind_speed_m_s)
    wind_to_rad = radians((parameters.wind_from_deg + 180.0) % 360.0)
    return tilt_angle, sin(wind_to_rad), cos(wind_to_rad)


def _check_outgoing_budget(
    grid: VillageGridSpec,
    sources: NDArray[np.int32],
    targets: NDArray[np.int32],
    weights: NDArray[np.float64],
    receiver_area_m2: NDArray[np.float64],
    parameters: V3RadiativeTransportParameters,
    profile_id: NDArray[np.str_] | None,
) -> None:
    sums = np.bincount(sources, weights=weights, minlength=grid.height * grid.width)
    offenders = np.flatnonzero(sums > 1.0 + 1e-12)
    if offenders.size == 0:
        return
    source_id = int(offenders[np.argmax(sums[offenders])])
    row, col = grid.row_col(source_id)
    connected = targets[sources == source_id]
    connected_area = float(np.sum(receiver_area_m2.ravel()[connected]))
    profile = "unknown"
    if profile_id is not None:
        identifiers = np.asarray(profile_id)
        if identifiers.shape != grid.shape:
            raise ValueError("profile_id must match the grid")
        profile = str(identifiers[row, col]) or "unknown"
    raise ValueError(
        "RADIATION_BUDGET_EXCEEDED: source cell "
        f"({row}, {col}) profile {profile!r} allocates {sums[source_id]:.12g} of "
        f"radiative power to {connected.size} targets with {connected_area:.6g} m^2 "
        f"combined receiver area inside radius {parameters.exposure_radius_m:.6g} m"
    )


def _empty_edges(grid: VillageGridSpec) -> ExposureEdges:
    return ExposureEdges(
        source=np.empty(0, dtype=np.int32),
        target=np.empty(0, dtype=np.int32),
        weight=np.empty(0, dtype=np.float64),
        cell_count=grid.height * grid.width,
    )


def _ground_elevation(grid: VillageGridSpec, value) -> np.ndarray:
    """Terrain is an elevation datum, separate from relative flame/receiver heights."""
    if value is None:
        return np.zeros(grid.shape, dtype=np.float64)
    array = np.asarray(value, dtype=np.float64)
    if array.shape != grid.shape or not np.isfinite(array).all():
        raise ValueError("transport ground elevation must be finite and match the grid")
    return array


def build_v3_radiative_transport_edges_reference(
    grid: VillageGridSpec,
    source_mask: NDArray[np.bool_],
    target_mask: NDArray[np.bool_],
    receiver_area_m2: NDArray[np.floating],
    source_height_m: NDArray[np.floating],
    receiver_height_m: NDArray[np.floating],
    blocker_transmissivity: NDArray[np.floating],
    parameters: V3RadiativeTransportParameters,
    *,
    profile_id: NDArray[np.str_] | None = None,
    ground_elevation_m: NDArray[np.floating] | None = None,
) -> ExposureEdges:
    """Small-domain oracle with heights above ground and a separate terrain datum."""

    source, target, area, source_height, receiver_height, transmission = _validate_transport_arrays(
        grid,
        source_mask,
        target_mask,
        receiver_area_m2,
        source_height_m,
        receiver_height_m,
        blocker_transmissivity,
    )
    offsets = _offsets(grid, parameters)
    ground = _ground_elevation(grid, ground_elevation_m)
    tilt, wind_sin, wind_cos = _wind_geometry(parameters)
    sources: list[int] = []
    targets: list[int] = []
    weights: list[float] = []
    for source_row_raw, source_col_raw in np.argwhere(source):
        source_row, source_col = int(source_row_raw), int(source_col_raw)
        half_flame = 0.5 * source_height[source_row, source_col]
        horizontal_shift = half_flame * sin(tilt)
        source_dx = horizontal_shift * wind_sin
        source_dy = horizontal_shift * wind_cos
        source_z = half_flame * cos(tilt)
        for drow, dcol, path in offsets:
            target_row, target_col = source_row + drow, source_col + dcol
            if not (0 <= target_row < grid.height and 0 <= target_col < grid.width):
                continue
            if not target[target_row, target_col]:
                continue
            visibility = 1.0
            for path_row, path_col in path:
                visibility *= transmission[source_row + path_row, source_col + path_col]
                if visibility == 0.0:
                    break
            if visibility == 0.0:
                continue
            delta_x = dcol * grid.cell_size_m - source_dx
            delta_y = -drow * grid.cell_size_m - source_dy
            distance = float(
                np.sqrt(
                    delta_x**2
                    + delta_y**2
                    + (
                        ground[target_row, target_col]
                        - ground[source_row, source_col]
                        + receiver_height[target_row, target_col]
                        - source_z
                    )
                    ** 2
                )
            )
            denominator = distance**2 + parameters.near_field_regularization_m**2
            if denominator <= 0.0:
                raise ValueError("v3 point-source distance is singular")
            weight = (
                area[target_row, target_col]
                / (4.0 * pi * denominator)
                * exp(-parameters.atmospheric_extinction_m_inv * distance)
                * visibility
            )
            if weight > 0.0:
                sources.append(grid.cell_id(source_row, source_col))
                targets.append(grid.cell_id(target_row, target_col))
                weights.append(weight)
    if not sources:
        return _empty_edges(grid)
    source_ids = np.asarray(sources, dtype=np.int32)
    target_ids = np.asarray(targets, dtype=np.int32)
    edge_weights = np.asarray(weights, dtype=np.float64)
    _check_outgoing_budget(grid, source_ids, target_ids, edge_weights, area, parameters, profile_id)
    return ExposureEdges(source_ids, target_ids, edge_weights, grid.height * grid.width)


def build_v3_radiative_transport_edges(
    grid: VillageGridSpec,
    source_mask: NDArray[np.bool_],
    target_mask: NDArray[np.bool_],
    receiver_area_m2: NDArray[np.floating],
    source_height_m: NDArray[np.floating],
    receiver_height_m: NDArray[np.floating],
    blocker_transmissivity: NDArray[np.floating],
    parameters: V3RadiativeTransportParameters,
    *,
    max_edge_count: int = DEFAULT_MAX_V3_TRANSPORT_EDGES,
    max_memory_mb: int = 2048,
    block_rows: int = 64,
    profile_id: NDArray[np.str_] | None = None,
    ground_elevation_m: NDArray[np.floating] | None = None,
) -> ExposureEdges:
    """Build edges from relative flame/receiver heights and optional ground elevation.

    Source height is the flame length above local ground: its radiative centre
    lies halfway along the tilted flame. Do not add absolute elevation to it.
    """

    source, target, area, source_height, receiver_height, transmission = _validate_transport_arrays(
        grid,
        source_mask,
        target_mask,
        receiver_area_m2,
        source_height_m,
        receiver_height_m,
        blocker_transmissivity,
    )
    ground = _ground_elevation(grid, ground_elevation_m)
    if isinstance(block_rows, bool) or not isinstance(block_rows, int) or block_rows <= 0:
        raise ValueError("block_rows must be a positive integer")
    estimate = estimate_v3_transport_resources(grid, source, target, parameters)
    enforce_v3_transport_resource_limits(
        estimate, max_edge_count=max_edge_count, max_memory_mb=max_memory_mb
    )
    capacity = estimate.candidate_edge_upper_bound
    if capacity == 0:
        return _empty_edges(grid)
    source_ids = np.empty(capacity, dtype=np.int32)
    target_ids = np.empty(capacity, dtype=np.int32)
    edge_weights = np.empty(capacity, dtype=np.float64)
    cursor = 0
    tilt, wind_sin, wind_cos = _wind_geometry(parameters)
    flat_width = grid.width
    for drow, dcol, path in _offsets(grid, parameters):
        source_rows, source_cols, _, target_cols = _pair_slices(grid, drow, dcol)
        for row_start in range(source_rows.start, source_rows.stop, block_rows):
            row_stop = min(source_rows.stop, row_start + block_rows)
            target_row_start, target_row_stop = row_start + drow, row_stop + drow
            pair_mask = (
                source[row_start:row_stop, source_cols]
                & target[target_row_start:target_row_stop, target_cols]
            )
            local_rows, local_cols = np.nonzero(pair_mask)
            if local_rows.size == 0:
                continue
            source_row = local_rows + row_start
            source_col = local_cols + source_cols.start
            target_row = source_row + drow
            target_col = source_col + dcol
            visibility = np.ones(local_rows.size, dtype=np.float64)
            for path_row, path_col in path:
                visibility *= transmission[source_row + path_row, source_col + path_col]
            keep = visibility > 0.0
            if not np.any(keep):
                continue
            source_row, source_col = source_row[keep], source_col[keep]
            target_row, target_col = target_row[keep], target_col[keep]
            visibility = visibility[keep]
            half_flame = 0.5 * source_height[source_row, source_col]
            horizontal_shift = half_flame * sin(tilt)
            delta_x = dcol * grid.cell_size_m - horizontal_shift * wind_sin
            delta_y = -drow * grid.cell_size_m - horizontal_shift * wind_cos
            source_z = half_flame * cos(tilt)
            distance = np.sqrt(
                delta_x**2
                + delta_y**2
                + (
                    ground[target_row, target_col]
                    - ground[source_row, source_col]
                    + receiver_height[target_row, target_col]
                    - source_z
                )
                ** 2
            )
            denominator = distance**2 + parameters.near_field_regularization_m**2
            if np.any(denominator <= 0.0):
                raise ValueError("v3 point-source distance is singular")
            weight = (
                area[target_row, target_col]
                / (4.0 * pi * denominator)
                * np.exp(-parameters.atmospheric_extinction_m_inv * distance)
                * visibility
            )
            keep = weight > 0.0
            count = int(np.count_nonzero(keep))
            if count == 0:
                continue
            stop = cursor + count
            source_ids[cursor:stop] = (source_row[keep] * flat_width + source_col[keep]).astype(
                np.int32
            )
            target_ids[cursor:stop] = (target_row[keep] * flat_width + target_col[keep]).astype(
                np.int32
            )
            edge_weights[cursor:stop] = weight[keep]
            cursor = stop
    source_view = source_ids[:cursor]
    target_view = target_ids[:cursor]
    weight_view = edge_weights[:cursor]
    _check_outgoing_budget(
        grid, source_view, target_view, weight_view, area, parameters, profile_id
    )
    return ExposureEdges(source_view, target_view, weight_view, grid.height * grid.width)


def write_v3_transport_resource_report(
    case: VegetationCase,
    physics: ResolvedV3Physics,
    output_dir: str | Path,
    *,
    blocker_transmissivity: NDArray[np.floating],
    blocker_assumption: str,
) -> Path:
    """Write the V04 whole-village resource and radiation-budget assessment."""

    if not isinstance(case, VegetationCase) or not isinstance(physics, ResolvedV3Physics):
        raise TypeError("case and physics must be resolved V03 objects")
    if not isinstance(blocker_assumption, str) or not blocker_assumption.strip():
        raise ValueError("blocker_assumption must be a non-empty explicit description")
    end_minutes = case.scenario["end_time_min"].value
    interval_minutes = case.scenario["sample_interval_min"].value
    quotient = end_minutes / interval_minutes
    replay_frames = int(np.floor(quotient + 1e-12)) + 1
    if not np.isclose(quotient, round(quotient), atol=1e-12):
        replay_frames += 1
    source = case.burnable_mask & (physics.arrays.peak_hrr_W > 0.0)
    target = case.burnable_mask & (physics.arrays.receiver_area_m2 > 0.0)
    transmission = _validated_float_array(
        "blocker_transmissivity", blocker_transmissivity, case.grid.shape
    )
    if np.any(transmission > 1.0):
        raise ValueError("blocker_transmissivity must lie in [0, 1]")
    parameters = parameters_from_case(case)
    estimate = estimate_v3_transport_resources(
        case.grid, source, target, parameters, replay_frame_count=replay_frames
    )
    limit_error = None
    try:
        enforce_v3_transport_resource_limits(
            estimate,
            max_edge_count=case.resources["max_edge_count"],
            max_memory_mb=case.resources["max_memory_mb"],
        )
    except ValueError as exc:
        limit_error = str(exc)
    graph_error = None
    graph_edge_count = None
    maximum_outgoing_fraction = None
    if limit_error is None:
        try:
            edges = build_v3_radiative_transport_edges(
                case.grid,
                source,
                target,
                physics.arrays.receiver_area_m2,
                physics.arrays.source_height_m,
                physics.arrays.receiver_height_m,
                transmission,
                parameters,
                max_edge_count=case.resources["max_edge_count"],
                max_memory_mb=case.resources["max_memory_mb"],
                profile_id=physics.profile_id,
            )
            graph_edge_count = len(edges.source)
            outgoing = np.bincount(
                edges.source,
                weights=edges.weight,
                minlength=case.grid.height * case.grid.width,
            )
            maximum_outgoing_fraction = float(np.max(outgoing, initial=0.0))
        except ValueError as exc:
            graph_error = str(exc)
    output = Path(output_dir).resolve()
    if output.exists():
        raise FileExistsError(f"transport report directory already exists: {output}")
    output.mkdir(parents=True)
    try:
        report = {
            "schema_version": "village-vegetation-v04-resource-report-1",
            "case_id": case.case_id,
            "stage": "V04",
            "simulation_executed": False,
            "graph_construction_attempted": limit_error is None,
            "graph_constructed": graph_edge_count is not None,
            "graph_edge_count": graph_edge_count,
            "radiation_budget_valid": (graph_error is None if limit_error is None else None),
            "maximum_outgoing_radiative_fraction": maximum_outgoing_fraction,
            "graph_validation_error": graph_error,
            "source_and_target_policy": "all classified burnable cells in one shared graph",
            "blocker_assumption": blocker_assumption,
            "transport_parameters": asdict(parameters),
            "configured_limits": dict(case.resources),
            "within_configured_graph_limits": limit_error is None,
            "resource_limit_error": limit_error,
            "estimate": estimate.as_dict(),
            "memory_model": {
                "final_edge_bytes": BYTES_PER_FINAL_EDGE,
                "construction_bytes_per_candidate_edge": (
                    CONSERVATIVE_BUILD_BYTES_PER_CANDIDATE_EDGE
                ),
                "replay_bytes_per_cell_frame": REPLAY_BYTES_PER_CELL_FRAME,
                "note": "replay estimate assumes four float64 fields and one uint8 state",
            },
        }
        (output / "resource_report.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        manifest = {
            "schema_version": "prepared-village-vegetation-transport-forecast-1",
            "stage": "V04",
            "case_id": case.case_id,
            "artifacts": ["resource_report.json"],
            "simulation_executed": False,
            "graph_constructed": graph_edge_count is not None,
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except Exception:
        shutil.rmtree(output)
        raise
    return output


__all__ = [
    "DEFAULT_MAX_V3_TRANSPORT_EDGES",
    "V3RadiativeTransportParameters",
    "V3TransportResourceEstimate",
    "build_v3_radiative_transport_edges",
    "build_v3_radiative_transport_edges_reference",
    "enforce_v3_transport_resource_limits",
    "estimate_v3_transport_resources",
    "parameters_from_case",
    "write_v3_transport_resource_report",
]
