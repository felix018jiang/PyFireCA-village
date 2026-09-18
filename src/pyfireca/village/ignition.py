"""External ignition events for village simulations."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from pyfireca.village.domain import VillageGridSpec


@dataclass(frozen=True, slots=True, order=True)
class VillageIgnitionEvent:
    time_s: float
    row: int
    col: int
    kind: str = "direct"

    def validate(self, grid: VillageGridSpec, end_time_s: float) -> None:
        if not isfinite(self.time_s) or not 0.0 <= self.time_s <= end_time_s:
            raise ValueError("ignition time must be finite and inside the simulation interval")
        if self.kind != "direct":
            raise ValueError("the version-1 village model only supports direct ignitions")
        grid.cell_id(self.row, self.col)
