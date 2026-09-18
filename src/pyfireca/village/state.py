"""Dynamic state definitions for village-fire simulations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from math import isfinite

import numpy as np
from numpy.typing import NDArray

from pyfireca.village.domain import PreparedVillageDomain


class VillageCellState(IntEnum):
    INERT = 0
    UNBURNED = 1
    HEATING = 2
    BURNING = 3
    BURNED_OUT = 4
    UNKNOWN = 254
    OUTSIDE = 255


@dataclass(slots=True)
class VillageState:
    time_s: float
    step_index: int
    state: NDArray[np.uint8]
    fuel_remaining_kg: NDArray[np.float64]
    exposure_J_m2: NDArray[np.float64]
    ignition_time_s: NDArray[np.float64]
    burnout_time_s: NDArray[np.float64]
    hrr_W: NDArray[np.float64]

    def copy(self) -> VillageState:
        return VillageState(
            time_s=self.time_s,
            step_index=self.step_index,
            state=self.state.copy(),
            fuel_remaining_kg=self.fuel_remaining_kg.copy(),
            exposure_J_m2=self.exposure_J_m2.copy(),
            ignition_time_s=self.ignition_time_s.copy(),
            burnout_time_s=self.burnout_time_s.copy(),
            hrr_W=self.hrr_W.copy(),
        )

    def validate(self, domain: PreparedVillageDomain) -> None:
        if not isfinite(self.time_s) or self.time_s < 0.0:
            raise ValueError("time_s must be finite and non-negative")
        if isinstance(self.step_index, bool) or not isinstance(self.step_index, int):
            raise TypeError("step_index must be an integer")
        if self.step_index < 0:
            raise ValueError("step_index must be non-negative")
        shape = domain.grid.shape
        state = np.asarray(self.state)
        if state.shape != shape or not np.issubdtype(state.dtype, np.integer):
            raise TypeError("state must be an integer array matching grid shape")
        valid = {int(value) for value in VillageCellState}
        if any(int(value) not in valid for value in np.unique(state)):
            raise ValueError("state contains an unsupported village state code")
        numeric = (
            "fuel_remaining_kg",
            "exposure_J_m2",
            "ignition_time_s",
            "burnout_time_s",
            "hrr_W",
        )
        for name in numeric:
            array = np.asarray(getattr(self, name), dtype=np.float64)
            if array.shape != shape:
                raise ValueError(f"{name} must match grid shape")
            if np.any(np.isnan(array)) or np.any(np.isneginf(array)):
                raise ValueError(f"{name} cannot contain NaN or -inf")
        if np.any(self.fuel_remaining_kg < -1e-12):
            raise ValueError("fuel_remaining_kg cannot be negative")
        if np.any(self.exposure_J_m2 < -1e-12) or np.any(self.hrr_W < -1e-12):
            raise ValueError("exposure and HRR cannot be negative")


def build_initial_village_state(domain: PreparedVillageDomain) -> VillageState:
    """Create a zero-time state from a prepared domain."""

    shape = domain.grid.shape
    state = np.full(shape, int(VillageCellState.OUTSIDE), dtype=np.uint8)
    unknown = domain.domain_mask & ~domain.known_mask
    inert = domain.known_mask & ~domain.burnable_mask
    state[unknown] = int(VillageCellState.UNKNOWN)
    state[inert] = int(VillageCellState.INERT)
    state[domain.burnable_mask] = int(VillageCellState.UNBURNED)
    fuel = domain.parameters.fuel_load_kg_m2 * domain.grid.cell_area_m2
    fuel = np.where(domain.burnable_mask, fuel, 0.0)
    dynamic = VillageState(
        time_s=0.0,
        step_index=0,
        state=state,
        fuel_remaining_kg=fuel,
        exposure_J_m2=np.zeros(shape, dtype=np.float64),
        ignition_time_s=np.full(shape, np.inf, dtype=np.float64),
        burnout_time_s=np.full(shape, np.inf, dtype=np.float64),
        hrr_W=np.zeros(shape, dtype=np.float64),
    )
    dynamic.validate(domain)
    return dynamic
