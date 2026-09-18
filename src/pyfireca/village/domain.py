"""Static array contracts for the village-fire simulation kernel."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from types import MappingProxyType
from typing import Any

import numpy as np
from numpy.typing import NDArray


def _positive(name: str, value: float) -> None:
    if not isfinite(value) or value <= 0.0:
        raise ValueError(f"{name} must be finite and positive")


def _float_array(name: str, value: NDArray[np.floating], shape: tuple[int, int]) -> NDArray:
    array = np.asarray(value, dtype=np.float64)
    if array.shape != shape:
        raise ValueError(f"{name} shape {array.shape} does not match grid shape {shape}")
    if not np.all(np.isfinite(array)):
        raise ValueError(f"{name} must contain only finite values")
    if np.any(array < 0.0):
        raise ValueError(f"{name} must be non-negative")
    return array.copy()


@dataclass(frozen=True, slots=True)
class VillageGridSpec:
    """North-up square metric grid used by the village model."""

    height: int
    width: int
    cell_size_m: float
    x_min_m: float
    y_max_m: float
    coordinate_mode: str = "local_metric"
    crs: str | None = None

    def __post_init__(self) -> None:
        for name, value in (("height", self.height), ("width", self.width)):
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        _positive("cell_size_m", self.cell_size_m)
        if not isfinite(self.x_min_m) or not isfinite(self.y_max_m):
            raise ValueError("grid origin must be finite")
        if self.coordinate_mode not in {"local_metric", "projected"}:
            raise ValueError("coordinate_mode must be 'local_metric' or 'projected'")
        if self.coordinate_mode == "projected" and not self.crs:
            raise ValueError("projected grids require a non-empty crs")
        if self.coordinate_mode == "local_metric" and self.crs is not None:
            raise ValueError("local_metric grids must not claim a crs")

    @property
    def shape(self) -> tuple[int, int]:
        return self.height, self.width

    @property
    def cell_area_m2(self) -> float:
        return self.cell_size_m**2

    def cell_id(self, row: int, col: int) -> int:
        if not 0 <= row < self.height or not 0 <= col < self.width:
            raise IndexError(f"cell ({row}, {col}) lies outside grid shape {self.shape}")
        return row * self.width + col

    def row_col(self, cell_id: int) -> tuple[int, int]:
        if isinstance(cell_id, bool) or not isinstance(cell_id, int):
            raise TypeError("cell_id must be an integer")
        if not 0 <= cell_id < self.height * self.width:
            raise IndexError(f"cell_id {cell_id} lies outside grid")
        return divmod(cell_id, self.width)

    def center(self, row: int, col: int) -> tuple[float, float]:
        self.cell_id(row, col)
        return (
            self.x_min_m + (col + 0.5) * self.cell_size_m,
            self.y_max_m - (row + 0.5) * self.cell_size_m,
        )

    def locate(self, x_m: float, y_m: float) -> tuple[int, int]:
        if not isfinite(x_m) or not isfinite(y_m):
            raise ValueError("coordinates must be finite")
        col = int(np.floor((x_m - self.x_min_m) / self.cell_size_m))
        row = int(np.floor((self.y_max_m - y_m) / self.cell_size_m))
        self.cell_id(row, col)
        return row, col


@dataclass(frozen=True, slots=True)
class CellParameterArrays:
    """Resolved per-cell parameters consumed by the numerical kernel."""

    fuel_load_kg_m2: NDArray[np.float64]
    heat_of_combustion_J_kg: NDArray[np.float64]
    mass_loss_flux_kg_m2_s: NDArray[np.float64]
    active_area_ratio: NDArray[np.float64]
    emission_fraction: NDArray[np.float64]
    absorptivity: NDArray[np.float64]
    ignition_dose_J_m2: NDArray[np.float64]
    exposure_decay_s: NDArray[np.float64]

    def validated(self, shape: tuple[int, int], burnable: NDArray[np.bool_]) -> CellParameterArrays:
        values = {
            field: _float_array(field, getattr(self, field), shape)
            for field in self.__dataclass_fields__
        }
        for bounded in ("active_area_ratio", "emission_fraction", "absorptivity"):
            if np.any(values[bounded] > 1.0):
                raise ValueError(f"{bounded} must lie in [0, 1]")
        required_positive = (
            "fuel_load_kg_m2",
            "heat_of_combustion_J_kg",
            "mass_loss_flux_kg_m2_s",
            "active_area_ratio",
            "ignition_dose_J_m2",
            "exposure_decay_s",
        )
        for name in required_positive:
            if np.any(values[name][burnable] <= 0.0):
                raise ValueError(f"{name} must be positive in every burnable cell")
        return CellParameterArrays(**values)


@dataclass(frozen=True, slots=True)
class PreparedVillageDomain:
    """Validated static data shared by village-fire scenarios."""

    grid: VillageGridSpec
    material_index: NDArray[np.int32]
    domain_mask: NDArray[np.bool_]
    known_mask: NDArray[np.bool_]
    burnable_mask: NDArray[np.bool_]
    parameters: CellParameterArrays
    provenance: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        shape = self.grid.shape
        material = np.asarray(self.material_index)
        if material.shape != shape or not np.issubdtype(material.dtype, np.integer):
            raise TypeError("material_index must be an integer array matching grid shape")
        masks: dict[str, NDArray[np.bool_]] = {}
        for name in ("domain_mask", "known_mask", "burnable_mask"):
            mask = np.asarray(getattr(self, name))
            if mask.shape != shape or mask.dtype != np.bool_:
                raise TypeError(f"{name} must be a boolean array matching grid shape")
            masks[name] = mask.copy()
        if np.any(masks["known_mask"] & ~masks["domain_mask"]):
            raise ValueError("known cells must lie inside the simulation domain")
        if np.any(masks["burnable_mask"] & ~masks["known_mask"]):
            raise ValueError("burnable cells must be known domain cells")
        if np.any(material[masks["known_mask"]] < 0):
            raise ValueError("known cells require a non-negative material index")
        parameters = self.parameters.validated(shape, masks["burnable_mask"])
        object.__setattr__(self, "material_index", material.astype(np.int32, copy=True))
        for name, mask in masks.items():
            object.__setattr__(self, name, mask)
        object.__setattr__(self, "parameters", parameters)
        object.__setattr__(self, "provenance", MappingProxyType(dict(self.provenance or {})))


@dataclass(frozen=True, slots=True)
class ExposureEdges:
    """Directed, scenario-specific source-to-target exposure weights."""

    source: NDArray[np.int32]
    target: NDArray[np.int32]
    weight: NDArray[np.float64]
    cell_count: int

    def __post_init__(self) -> None:
        if isinstance(self.cell_count, bool) or not isinstance(self.cell_count, int):
            raise TypeError("cell_count must be an integer")
        if self.cell_count <= 0:
            raise ValueError("cell_count must be positive")
        source = np.asarray(self.source)
        target = np.asarray(self.target)
        weight = np.asarray(self.weight, dtype=np.float64)
        if source.ndim != 1 or target.ndim != 1 or weight.ndim != 1:
            raise ValueError("edge arrays must be one-dimensional")
        if not (len(source) == len(target) == len(weight)):
            raise ValueError("edge arrays must have equal length")
        if not np.issubdtype(source.dtype, np.integer) or not np.issubdtype(
            target.dtype, np.integer
        ):
            raise TypeError("edge source and target must use integer dtypes")
        if np.any(source < 0) or np.any(source >= self.cell_count):
            raise ValueError("edge source contains an out-of-range cell id")
        if np.any(target < 0) or np.any(target >= self.cell_count):
            raise ValueError("edge target contains an out-of-range cell id")
        if np.any(source == target):
            raise ValueError("self edges are not allowed")
        if not np.all(np.isfinite(weight)) or np.any(weight < 0.0):
            raise ValueError("edge weights must be finite and non-negative")
        sums = np.bincount(source, weights=weight, minlength=self.cell_count)
        if np.any(sums > 1.0 + 1e-12):
            raise ValueError("outgoing edge weights may not sum above one")
        order = np.lexsort((target, source))
        object.__setattr__(self, "source", source[order].astype(np.int32, copy=True))
        object.__setattr__(self, "target", target[order].astype(np.int32, copy=True))
        object.__setattr__(self, "weight", weight[order].copy())
