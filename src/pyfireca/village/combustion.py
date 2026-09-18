"""Finite-fuel combustion helpers for the village model."""

from __future__ import annotations

import numpy as np
from numpy.typing import NDArray


def burning_mass_loss_and_hrr(
    burning: NDArray[np.bool_],
    fuel_remaining_kg: NDArray[np.floating],
    mass_loss_rate_kg_s: NDArray[np.floating],
    heat_of_combustion_J_kg: NDArray[np.floating],
    dt_s: float,
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """Return interval mass loss and mean HRR without mutating inputs."""

    if not np.isfinite(dt_s) or dt_s <= 0.0:
        raise ValueError("dt_s must be finite and positive")
    burning = np.asarray(burning)
    remaining = np.asarray(fuel_remaining_kg, dtype=np.float64)
    rate = np.asarray(mass_loss_rate_kg_s, dtype=np.float64)
    heat = np.asarray(heat_of_combustion_J_kg, dtype=np.float64)
    if burning.dtype != np.bool_:
        raise TypeError("burning must be a boolean array")
    if not (burning.shape == remaining.shape == rate.shape == heat.shape):
        raise ValueError("combustion arrays must have equal shape")
    if (
        np.any(~np.isfinite(remaining))
        or np.any(~np.isfinite(rate))
        or np.any(~np.isfinite(heat))
        or np.any(remaining < 0.0)
        or np.any(rate < 0.0)
        or np.any(heat < 0.0)
    ):
        raise ValueError("combustion inputs must be finite and non-negative")
    loss = np.minimum(remaining, np.where(burning, rate * dt_s, 0.0))
    return loss, loss / dt_s * heat


def maximum_fuel_limited_step_s(
    burning: NDArray[np.bool_],
    fuel_remaining_kg: NDArray[np.floating],
    mass_loss_rate_kg_s: NDArray[np.floating],
) -> float:
    """Return the earliest positive exhaustion time among burning cells."""

    burning = np.asarray(burning, dtype=np.bool_)
    remaining = np.asarray(fuel_remaining_kg, dtype=np.float64)
    rate = np.asarray(mass_loss_rate_kg_s, dtype=np.float64)
    active = burning & (remaining > 0.0) & (rate > 0.0)
    if not np.any(active):
        return np.inf
    return float(np.min(remaining[active] / rate[active]))
