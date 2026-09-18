"""Effective exposure-edge generation and dose integration."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, pi

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.domain import ExposureEdges, PreparedVillageDomain

DEFAULT_MAX_EXPOSURE_EDGES = 5_000_000


@dataclass(frozen=True, slots=True)
class ExposureResourceEstimate:
    """Conservative sizes available before allocating the exposure graph."""

    cell_count: int
    burnable_cell_count: int
    neighbor_offset_count: int
    exposure_edge_upper_bound: int
    exposure_arrays_upper_bound_bytes: int
    state_frame_bytes: int

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def estimate_exposure_resources(
    domain: PreparedVillageDomain, *, exposure_radius_m: float
) -> ExposureResourceEstimate:
    """Estimate the worst-case graph size for the configured radius."""

    if not np.isfinite(exposure_radius_m) or exposure_radius_m <= 0.0:
        raise ValueError("exposure_radius_m must be finite and positive")
    grid = domain.grid
    max_offset = int(np.floor(exposure_radius_m / grid.cell_size_m))
    offset_count = 0
    for drow in range(-max_offset, max_offset + 1):
        for dcol in range(-max_offset, max_offset + 1):
            if drow == 0 and dcol == 0:
                continue
            if np.hypot(drow, dcol) * grid.cell_size_m <= exposure_radius_m + 1e-12:
                offset_count += 1
    burnable_count = int(np.count_nonzero(domain.burnable_mask))
    edge_upper_bound = burnable_count * offset_count
    return ExposureResourceEstimate(
        cell_count=grid.height * grid.width,
        burnable_cell_count=burnable_count,
        neighbor_offset_count=offset_count,
        exposure_edge_upper_bound=edge_upper_bound,
        exposure_arrays_upper_bound_bytes=edge_upper_bound * 16,
        state_frame_bytes=grid.height * grid.width,
    )


def build_exposure_edges(
    domain: PreparedVillageDomain,
    *,
    exposure_radius_m: float,
    decay_length_m: float,
    wind_speed_m_s: float = 0.0,
    wind_from_deg: float = 0.0,
    wind_beta_s_m: float = 0.0,
    max_edge_count: int = DEFAULT_MAX_EXPOSURE_EDGES,
) -> ExposureEdges:
    """Build deterministic directed weights for the version-1 planar kernel."""

    scalars = (exposure_radius_m, decay_length_m, wind_speed_m_s, wind_from_deg, wind_beta_s_m)
    if not all(np.isfinite(value) for value in scalars):
        raise ValueError("exposure kernel inputs must be finite")
    if exposure_radius_m <= 0.0 or decay_length_m <= 0.0:
        raise ValueError("exposure_radius_m and decay_length_m must be positive")
    if wind_speed_m_s < 0.0 or wind_beta_s_m < 0.0:
        raise ValueError("wind speed and beta must be non-negative")
    if not 0.0 <= wind_from_deg < 360.0:
        raise ValueError("wind_from_deg must lie in [0, 360)")
    if isinstance(max_edge_count, bool) or not isinstance(max_edge_count, int):
        raise TypeError("max_edge_count must be an integer")
    if max_edge_count <= 0:
        raise ValueError("max_edge_count must be positive")

    grid = domain.grid
    estimate = estimate_exposure_resources(domain, exposure_radius_m=exposure_radius_m)
    if estimate.exposure_edge_upper_bound > max_edge_count:
        raise ValueError(
            "RESOURCE_LIMIT: exposure graph upper bound "
            f"{estimate.exposure_edge_upper_bound:,} exceeds limit {max_edge_count:,}; "
            "increase cell_size_m, reduce exposure_radius_m, or reduce the domain"
        )
    max_offset = int(np.floor(exposure_radius_m / grid.cell_size_m))
    offsets: list[tuple[int, int, float, float]] = []
    wind_to = (wind_from_deg + 180.0) % 360.0
    for drow in range(-max_offset, max_offset + 1):
        for dcol in range(-max_offset, max_offset + 1):
            if drow == 0 and dcol == 0:
                continue
            distance = np.hypot(drow, dcol) * grid.cell_size_m
            if distance > exposure_radius_m + 1e-12:
                continue
            bearing = np.degrees(np.arctan2(float(dcol), float(-drow))) % 360.0
            alignment = np.cos(np.deg2rad(bearing - wind_to))
            wind_factor = exp(np.clip(wind_beta_s_m * wind_speed_m_s * alignment, -3, 3))
            base = grid.cell_area_m2 / (4.0 * pi * (distance**2 + grid.cell_area_m2 / pi))
            raw = base * exp(-distance / decay_length_m) * wind_factor
            offsets.append((drow, dcol, distance, raw))

    sources: list[int] = []
    targets: list[int] = []
    raw_weights: list[float] = []
    known = domain.known_mask
    for row, col in np.argwhere(domain.burnable_mask):
        src = grid.cell_id(int(row), int(col))
        local_targets: list[int] = []
        local_raw: list[float] = []
        for drow, dcol, _distance, raw in offsets:
            target_row = int(row) + drow
            target_col = int(col) + dcol
            if not (0 <= target_row < grid.height and 0 <= target_col < grid.width):
                continue
            if not known[target_row, target_col]:
                continue
            local_targets.append(grid.cell_id(target_row, target_col))
            local_raw.append(raw)
        denominator = max(1.0, sum(local_raw))
        sources.extend([src] * len(local_targets))
        targets.extend(local_targets)
        raw_weights.extend(value / denominator for value in local_raw)
        if len(sources) > max_edge_count:
            raise ValueError(f"RESOURCE_LIMIT: exposure graph exceeds {max_edge_count:,} edges")
    return ExposureEdges(
        source=np.asarray(sources, dtype=np.int32),
        target=np.asarray(targets, dtype=np.int32),
        weight=np.asarray(raw_weights, dtype=np.float64),
        cell_count=grid.height * grid.width,
    )


def target_power_W(
    emitted_power_W: NDArray[np.floating], edges: ExposureEdges
) -> NDArray[np.float64]:
    """Distribute source power over edges and sum it at targets."""

    power = np.asarray(emitted_power_W, dtype=np.float64).reshape(-1)
    if power.size != edges.cell_count:
        raise ValueError("emitted power size does not match edge cell_count")
    if np.any(~np.isfinite(power)) or np.any(power < 0.0):
        raise ValueError("emitted power must be finite and non-negative")
    contributions = power[edges.source] * edges.weight
    return np.bincount(edges.target, weights=contributions, minlength=edges.cell_count)


def integrate_exposure_dose(
    exposure_J_m2: NDArray[np.floating],
    heat_flux_W_m2: NDArray[np.floating],
    decay_time_s: NDArray[np.floating],
    dt_s: float,
) -> NDArray[np.float64]:
    """Integrate the constant-flux exponential effective-dose equation."""

    exposure = np.asarray(exposure_J_m2, dtype=np.float64)
    flux = np.asarray(heat_flux_W_m2, dtype=np.float64)
    decay = np.asarray(decay_time_s, dtype=np.float64)
    if not (exposure.shape == flux.shape == decay.shape):
        raise ValueError("dose arrays must have equal shape")
    if not np.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    if (
        np.any(~np.isfinite(exposure))
        or np.any(~np.isfinite(flux))
        or np.any(~np.isfinite(decay))
        or np.any(exposure < 0.0)
        or np.any(flux < 0.0)
        or np.any(decay <= 0.0)
    ):
        raise ValueError("dose inputs must be finite with valid non-negative values")
    decay_factor = np.exp(-dt_s / decay)
    response = -np.expm1(-dt_s / decay)
    return exposure * decay_factor + flux * decay * response
