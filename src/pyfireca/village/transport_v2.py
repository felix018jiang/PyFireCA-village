"""Geometry-based radiative transport kernel for village model v2."""

from __future__ import annotations

from dataclasses import dataclass
from math import atan, cos, exp, pi, radians, sin

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.domain import ExposureEdges, VillageGridSpec

DEFAULT_MAX_V2_TRANSPORT_EDGES = 5_000_000


@dataclass(frozen=True, slots=True)
class V2RadiativeTransportParameters:
    """Calibratable geometry used by the fixed point-source radiation kernel.

    The kernel interprets each burning cell as an isotropic effective point source at
    the centre of a tilted flame. Receiver area is an effective projected area. It is
    a reduced-order radiation model, not a flame-resolving calculation.
    """

    exposure_radius_m: float
    effective_flame_height_m: float
    effective_receiver_height_m: float
    near_field_regularization_m: float
    atmospheric_extinction_m_inv: float
    wind_speed_m_s: float
    wind_from_deg: float
    flame_tilt_coefficient_s_m: float

    def __post_init__(self) -> None:
        numeric = (
            self.exposure_radius_m,
            self.effective_flame_height_m,
            self.effective_receiver_height_m,
            self.near_field_regularization_m,
            self.atmospheric_extinction_m_inv,
            self.wind_speed_m_s,
            self.wind_from_deg,
            self.flame_tilt_coefficient_s_m,
        )
        if not all(np.isfinite(value) for value in numeric):
            raise ValueError("v2 transport parameters must be finite")
        if self.exposure_radius_m <= 0.0 or self.effective_flame_height_m <= 0.0:
            raise ValueError("v2 exposure radius and flame height must be positive")
        if (
            self.effective_receiver_height_m < 0.0
            or self.near_field_regularization_m < 0.0
            or self.atmospheric_extinction_m_inv < 0.0
            or self.wind_speed_m_s < 0.0
            or self.flame_tilt_coefficient_s_m < 0.0
        ):
            raise ValueError(
                "v2 receiver height, regularization, extinction, wind, and tilt "
                "must be non-negative"
            )
        if not 0.0 <= self.wind_from_deg < 360.0:
            raise ValueError("v2 wind_from_deg must lie in [0, 360)")


def _line_cells(row0: int, col0: int, row1: int, col1: int) -> tuple[tuple[int, int], ...]:
    """Return deterministic Bresenham cells from source through target."""

    cells: list[tuple[int, int]] = []
    delta_col = abs(col1 - col0)
    step_col = 1 if col0 < col1 else -1
    delta_row = -abs(row1 - row0)
    step_row = 1 if row0 < row1 else -1
    error = delta_col + delta_row
    row, col = row0, col0
    while True:
        cells.append((row, col))
        if row == row1 and col == col1:
            return tuple(cells)
        doubled = 2 * error
        if doubled >= delta_row:
            error += delta_row
            col += step_col
        if doubled <= delta_col:
            error += delta_col
            row += step_row


def build_v2_radiative_transport_edges(
    grid: VillageGridSpec,
    source_mask: NDArray[np.bool_],
    target_mask: NDArray[np.bool_],
    receiver_area_m2: NDArray[np.floating],
    blocker_transmissivity: NDArray[np.floating],
    parameters: V2RadiativeTransportParameters,
    *,
    max_edge_count: int = DEFAULT_MAX_V2_TRANSPORT_EDGES,
) -> ExposureEdges:
    """Build fixed directed radiation fractions from geometry and line transmission.

    For source ``i`` and target ``j`` the fraction is

    ``A_receiver,j / (4*pi*(R_ij**2+r0**2)) * exp(-kappa*R_ij) * product(tau_cells)``.

    Wind changes the effective source location through a calibrated flame-tilt angle.
    The outgoing fractions are checked against unity and are never renormalized.
    """

    source = np.asarray(source_mask)
    target = np.asarray(target_mask)
    area = np.asarray(receiver_area_m2, dtype=np.float64)
    transmission = np.asarray(blocker_transmissivity, dtype=np.float64)
    if source.shape != grid.shape or source.dtype != np.bool_:
        raise TypeError("v2 transport source_mask must be boolean and match the grid")
    if target.shape != grid.shape or target.dtype != np.bool_:
        raise TypeError("v2 transport target_mask must be boolean and match the grid")
    if area.shape != grid.shape or transmission.shape != grid.shape:
        raise ValueError("v2 receiver area and blocker transmission must match the grid")
    if np.any(~np.isfinite(area)) or np.any(area < 0.0):
        raise ValueError("v2 receiver area must be finite and non-negative")
    if np.any(~np.isfinite(transmission)) or np.any((transmission < 0.0) | (transmission > 1.0)):
        raise ValueError("v2 blocker transmissivity must lie in [0, 1]")
    if np.any(target & (area <= 0.0)):
        raise ValueError("every v2 transport target requires positive receiver area")
    if isinstance(max_edge_count, bool) or not isinstance(max_edge_count, int):
        raise TypeError("max_edge_count must be an integer")
    if max_edge_count <= 0:
        raise ValueError("max_edge_count must be positive")

    max_offset = int(np.floor(parameters.exposure_radius_m / grid.cell_size_m))
    offset_count = sum(
        1
        for drow in range(-max_offset, max_offset + 1)
        for dcol in range(-max_offset, max_offset + 1)
        if (drow or dcol)
        and np.hypot(drow, dcol) * grid.cell_size_m <= parameters.exposure_radius_m + 1e-12
    )
    upper_bound = int(np.count_nonzero(source)) * offset_count
    if upper_bound > max_edge_count:
        raise ValueError(
            "RESOURCE_LIMIT: v2 transport graph upper bound "
            f"{upper_bound:,} exceeds limit {max_edge_count:,}"
        )

    tilt_angle = atan(parameters.flame_tilt_coefficient_s_m * parameters.wind_speed_m_s)
    wind_to_rad = radians((parameters.wind_from_deg + 180.0) % 360.0)
    half_flame = 0.5 * parameters.effective_flame_height_m
    horizontal_shift = half_flame * sin(tilt_angle)
    source_dx = horizontal_shift * sin(wind_to_rad)
    source_dy = horizontal_shift * cos(wind_to_rad)
    source_z = half_flame * cos(tilt_angle)

    sources: list[int] = []
    targets: list[int] = []
    weights: list[float] = []
    for source_row, source_col in np.argwhere(source):
        source_row = int(source_row)
        source_col = int(source_col)
        source_x, source_y = grid.center(source_row, source_col)
        source_x += source_dx
        source_y += source_dy
        local_sum = 0.0
        for drow in range(-max_offset, max_offset + 1):
            for dcol in range(-max_offset, max_offset + 1):
                if drow == 0 and dcol == 0:
                    continue
                horizontal_distance = np.hypot(drow, dcol) * grid.cell_size_m
                if horizontal_distance > parameters.exposure_radius_m + 1e-12:
                    continue
                target_row = source_row + drow
                target_col = source_col + dcol
                if not (
                    0 <= target_row < grid.height
                    and 0 <= target_col < grid.width
                    and target[target_row, target_col]
                ):
                    continue
                visibility = 1.0
                for row, col in _line_cells(source_row, source_col, target_row, target_col)[1:-1]:
                    visibility *= transmission[row, col]
                    if visibility == 0.0:
                        break
                if visibility == 0.0:
                    continue
                target_x, target_y = grid.center(target_row, target_col)
                distance = np.sqrt(
                    (target_x - source_x) ** 2
                    + (target_y - source_y) ** 2
                    + (parameters.effective_receiver_height_m - source_z) ** 2
                )
                denominator_distance_squared = (
                    distance**2 + parameters.near_field_regularization_m**2
                )
                if denominator_distance_squared <= 0.0:
                    raise ValueError("v2 point-source distance is singular")
                weight = (
                    area[target_row, target_col]
                    / (4.0 * pi * denominator_distance_squared)
                    * exp(-parameters.atmospheric_extinction_m_inv * distance)
                    * visibility
                )
                if weight <= 0.0:
                    continue
                sources.append(grid.cell_id(source_row, source_col))
                targets.append(grid.cell_id(target_row, target_col))
                weights.append(weight)
                local_sum += weight
        if local_sum > 1.0 + 1e-12:
            raise ValueError(
                "v2 point-source transport allocates more than all radiative power; "
                "reduce receiver area/radius or calibrate the kernel"
            )
    return ExposureEdges(
        source=np.asarray(sources, dtype=np.int32),
        target=np.asarray(targets, dtype=np.int32),
        weight=np.asarray(weights, dtype=np.float64),
        cell_count=grid.height * grid.width,
    )


__all__ = [
    "DEFAULT_MAX_V2_TRANSPORT_EDGES",
    "V2RadiativeTransportParameters",
    "build_v2_radiative_transport_edges",
]
