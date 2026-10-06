"""Bounded near-field flame-bath closure, separate from radiation and embers.

Newton heat transfer is physical; the face-contact duty and gas properties are
explicit effective inputs requiring calibration. This is not resolved fluid
flow, a crown-transition model, or a validated wildfire spread-rate law.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import yaml

from pyfireca.village.domain import VillageGridSpec
from pyfireca.village.physics_v3 import V3CellPhysicsArrays

SCHEMA = "village-flame-contact-1"


@dataclass(frozen=True, slots=True)
class FlameContactParameters:
    gas_temperature_K: float
    heat_transfer_coefficient_W_m2_K: float
    contact_fraction: float
    max_cell_size_m: float
    evidence: str
    provenance: str
    receiver_height_overrides_m: tuple[tuple[int, float], ...] = ()
    receiver_vertical_half_extent_m: float = 0.0

    def __post_init__(self) -> None:
        for name in (
            "gas_temperature_K",
            "heat_transfer_coefficient_W_m2_K",
            "contact_fraction",
            "max_cell_size_m",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"flame contact {name} must be numeric")
            if not np.isfinite(value) or value <= 0:
                raise ValueError(f"flame contact {name} must be finite and positive")
        if self.contact_fraction > 1:
            raise ValueError("flame contact fraction must lie in (0, 1]")
        extent = self.receiver_vertical_half_extent_m
        if isinstance(extent, bool) or not isinstance(extent, (int, float)):
            raise TypeError("receiver vertical half extent must be numeric")
        if not np.isfinite(extent) or extent < 0:
            raise ValueError("receiver vertical half extent must be finite and nonnegative")
        if self.evidence not in {"synthetic", "literature_informed", "measured"}:
            raise ValueError("flame contact evidence must be explicit")
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError("flame contact provenance must be nonempty")
        if not isinstance(self.receiver_height_overrides_m, tuple):
            raise TypeError("contact receiver overrides must be an immutable tuple")
        seen = set()
        for item in self.receiver_height_overrides_m:
            if not isinstance(item, tuple) or len(item) != 2:
                raise ValueError("contact receiver override requires material ID and height")
            material, height = item
            if isinstance(material, bool) or not isinstance(material, int) or material < 0:
                raise ValueError("contact receiver material ID must be a nonnegative integer")
            if material in seen:
                raise ValueError("duplicate contact receiver material ID")
            seen.add(material)
            if (
                isinstance(height, bool)
                or not isinstance(height, (int, float))
                or not np.isfinite(height)
                or height <= 0
            ):
                raise ValueError("contact receiver height must be finite and positive")

    def to_mapping(self) -> dict:
        result = {"schema_version": SCHEMA, **asdict(self)}
        if not self.receiver_height_overrides_m:
            result.pop("receiver_height_overrides_m")
        if self.receiver_vertical_half_extent_m == 0:
            result.pop("receiver_vertical_half_extent_m")
        return result

    @classmethod
    def from_mapping(cls, value: dict) -> FlameContactParameters:
        if not isinstance(value, dict):
            raise TypeError("flame contact configuration must be a mapping")
        optional = {"receiver_height_overrides_m", "receiver_vertical_half_extent_m"}
        required = {"schema_version", *cls.__dataclass_fields__} - optional
        if not required <= set(value) or set(value) - required - optional:
            raise ValueError("flame contact configuration requires exact parameter keys")
        if value["schema_version"] != SCHEMA:
            raise ValueError("unsupported flame contact schema")
        arguments = {key: value[key] for key in cls.__dataclass_fields__ if key in value}
        if "receiver_height_overrides_m" in arguments:
            raw = arguments["receiver_height_overrides_m"]
            if not isinstance(raw, (tuple, list)) or any(
                not isinstance(item, (tuple, list)) for item in raw
            ):
                raise ValueError("contact receiver overrides must be pairs")
            arguments["receiver_height_overrides_m"] = tuple(tuple(item) for item in raw)
        return cls(**arguments)


def load_flame_contact_parameters(path: str | Path) -> FlameContactParameters:
    return FlameContactParameters.from_mapping(yaml.safe_load(Path(path).read_text()))


@dataclass(frozen=True, slots=True)
class FlameContactTransfer:
    target_power_W: np.ndarray
    target_contact_fraction: np.ndarray
    transferred_W: float
    available_nonradiative_W: float
    energy_limited_sources: int


@dataclass(frozen=True, slots=True)
class FlameContactOperator:
    """A contact graph with shared source budgets and bounded receiver area.

    ``build`` uses four face neighbors and effective ground-based source columns.
    The component resolver can supply same-cell/face-neighbor component edges
    using explicit source bases. Neither graph jumps an inert/excluded gap.
    Partial coverage assumes uniform mixing, not reconstructed plant geometry.
    """

    neighbors: np.ndarray
    geometry_fraction: np.ndarray
    parameters: FlameContactParameters
    physics: V3CellPhysicsArrays

    @classmethod
    def build(
        cls,
        grid: VillageGridSpec,
        burnable_mask: np.ndarray,
        physics: V3CellPhysicsArrays,
        parameters: FlameContactParameters,
        ground_elevation_m: np.ndarray | None = None,
        material_index: np.ndarray | None = None,
    ) -> FlameContactOperator:
        if grid.cell_size_m > parameters.max_cell_size_m + 1e-12:
            raise ValueError("flame contact grid exceeds its explicit maximum cell size")
        mask = np.asarray(burnable_mask)
        if mask.dtype != np.bool_ or mask.shape != grid.shape:
            raise TypeError("flame contact burnable mask must be boolean and match grid")
        count = grid.height * grid.width
        neighbors = np.full((count, 4), -1, dtype=np.int32)
        fraction = np.zeros((count, 4), dtype=np.float64)
        elevation = np.zeros(grid.shape) if ground_elevation_m is None else ground_elevation_m
        elevation = np.asarray(elevation, dtype=np.float64)
        if elevation.shape != grid.shape or np.any(~np.isfinite(elevation)):
            raise ValueError("flame contact terrain must be finite and match grid")
        ids = np.arange(count, dtype=np.int32).reshape(grid.shape)
        contact_height = physics.receiver_height_m.copy()
        if parameters.receiver_height_overrides_m:
            materials = np.asarray(material_index)
            if materials.shape != grid.shape or not np.issubdtype(materials.dtype, np.integer):
                raise ValueError("contact receiver overrides require a matching material grid")
            for material, height in parameters.receiver_height_overrides_m:
                contact_height[materials == material] = height
        for direction, (dr, dc) in enumerate(((-1, 0), (1, 0), (0, -1), (0, 1))):
            sr = slice(max(0, -dr), min(grid.height, grid.height - dr))
            sc = slice(max(0, -dc), min(grid.width, grid.width - dc))
            tr = slice(max(0, dr), min(grid.height, grid.height + dr))
            tc = slice(max(0, dc), min(grid.width, grid.width + dc))
            # Contact can reach a low combustible part while the radiation
            # receiver remains at its independently defined representative height.
            target_z = elevation[tr, tc] + contact_height[tr, tc]
            extent = parameters.receiver_vertical_half_extent_m
            if extent > 0:
                bottom = np.maximum(elevation[tr, tc], target_z - extent)
                top = target_z + extent
                overlap = np.maximum(
                    0.0,
                    np.minimum(top, elevation[sr, sc] + physics.source_height_m[sr, sc])
                    - np.maximum(bottom, elevation[sr, sc]),
                )
                # Sub-resolution bands round to a point at large elevations.
                contact_weight = (
                    (target_z >= elevation[sr, sc])
                    & (target_z <= elevation[sr, sc] + physics.source_height_m[sr, sc])
                ).astype(np.float64)
                np.divide(overlap, top - bottom, out=contact_weight, where=top > bottom)
            else:
                contact_weight = (
                    (target_z >= elevation[sr, sc])
                    & (target_z <= elevation[sr, sc] + physics.source_height_m[sr, sc])
                ).astype(np.float64)
            keep = mask[sr, sc] & mask[tr, tc] & (contact_weight > 0)
            source_ids = ids[sr, sc][keep]
            neighbors[source_ids, direction] = ids[tr, tc][keep]
            fraction[source_ids, direction] = (
                physics.coverage_fraction[sr, sc][keep] * contact_weight[keep]
            )
        neighbors.setflags(write=False)
        fraction.setflags(write=False)
        return cls(neighbors, fraction, parameters, physics)

    def transfer(
        self,
        mean_hrr_W: np.ndarray,
        temperature_K: np.ndarray,
        susceptible_mask: np.ndarray,
    ) -> FlameContactTransfer:
        """Limit contact area per target and heat extraction per active source."""
        shape = self.physics.peak_hrr_W.shape
        hrr = np.asarray(mean_hrr_W, dtype=np.float64)
        temperature = np.asarray(temperature_K, dtype=np.float64)
        susceptible = np.asarray(susceptible_mask)
        if hrr.shape != shape or temperature.shape != shape or susceptible.shape != shape:
            raise ValueError("flame contact state arrays must match physics")
        if susceptible.dtype != np.bool_:
            raise TypeError("flame contact susceptible mask must be boolean")
        if np.any(~np.isfinite(hrr)) or np.any(hrr < 0):
            raise ValueError("flame contact HRR must be finite and nonnegative")
        if np.any(hrr > self.physics.peak_hrr_W * (1 + 1e-8) + 1e-9):
            raise ValueError("flame contact HRR cannot exceed the configured peak")
        if np.any(~np.isfinite(temperature)) or np.any(temperature <= 0):
            raise ValueError("flame contact temperature must be finite and positive")
        size = hrr.size
        power = np.zeros(size)
        target_fraction = np.zeros(size)
        available = hrr.ravel() * (1 - self.physics.radiative_fraction.ravel())
        active = np.flatnonzero(available > 0)
        source = np.repeat(active, self.neighbors.shape[1])
        target = self.neighbors[active].ravel()
        fraction = self.geometry_fraction[active].ravel() * self.parameters.contact_fraction
        keep = target >= 0
        source, target, fraction = source[keep], target[keep], fraction[keep]
        keep = susceptible.ravel()[target] & (
            temperature.ravel()[target] < self.parameters.gas_temperature_K
        )
        source, target, fraction = source[keep], target[keep], fraction[keep]
        # Effective intermittency vanishes with HRR; there is no heat or pilot
        # from a zero-output or extinguished cell.
        activity = np.minimum(1.0, hrr.ravel()[source] / self.physics.peak_hrr_W.ravel()[source])
        fraction *= activity
        total_fraction = np.bincount(target, weights=fraction, minlength=size)
        fraction /= np.maximum(1.0, total_fraction[target])
        demand = (
            fraction
            * self.physics.covered_area_m2.ravel()[target]
            * self.parameters.heat_transfer_coefficient_W_m2_K
            * (self.parameters.gas_temperature_K - temperature.ravel()[target])
        )
        source_demand = np.bincount(source, weights=demand, minlength=size)
        limited = source_demand > available
        scale = np.ones(size)
        np.divide(available, source_demand, out=scale, where=limited)
        demand *= scale[source]
        fraction *= scale[source]
        np.add.at(power, target, demand)
        np.add.at(target_fraction, target, fraction)
        return FlameContactTransfer(
            power.reshape(shape),
            target_fraction.reshape(shape),
            float(np.sum(demand)),
            float(np.sum(available)),
            int(np.count_nonzero(limited)),
        )
