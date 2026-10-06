"""Explicit contracts for P01 static, local-metre intervention experiments."""

from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite
from numbers import Real
from typing import Literal

from pyfireca.arrival import DirectionalSpreadRateProvider

PointXY = tuple[float, float]


def require_number(name: str, value: float, *, positive: bool = False) -> None:
    """Reject booleans, nonfinite values and negative physical quantities."""
    if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
        raise ValueError(f"{name} must be a finite number")
    if value < 0 or (positive and value == 0):
        raise ValueError(f"{name} must be {'positive' if positive else 'non-negative'}")


def require_id(name: str, value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def require_point(point: PointXY) -> None:
    if not isinstance(point, tuple) or len(point) != 2:
        raise ValueError("coordinates must be a two-number tuple in local metres")
    for value in point:
        if isinstance(value, bool) or not isinstance(value, Real) or not isfinite(value):
            raise ValueError("coordinates must be finite numbers")


@dataclass(frozen=True, slots=True)
class Evidence:
    """Origin of a profile; a label does not establish validation or causality."""

    kind: Literal["synthetic", "model_derived", "measured"]
    source: str
    applicability: str

    def __post_init__(self) -> None:
        if self.kind not in ("synthetic", "model_derived", "measured"):
            raise ValueError("unsupported evidence kind")
        require_id("source", self.source)
        require_id("applicability", self.applicability)


@dataclass(frozen=True, slots=True)
class PlanningGrid:
    """North-up square grid in an explicitly local metric coordinate frame.

    This is not an EPSG adapter. Array rows increase southward; ``x_min_m`` and
    ``y_max_m`` specify the top-left *boundary*, not the first cell centre.
    """

    shape: tuple[int, int]
    cell_size_m: float
    x_min_m: float = 0.0
    y_max_m: float = 0.0
    coordinate_frame: str = "local_metre"

    def __post_init__(self) -> None:
        if (
            not isinstance(self.shape, tuple)
            or len(self.shape) != 2
            or any(isinstance(v, bool) or not isinstance(v, int) or v < 1 for v in self.shape)
        ):
            raise ValueError("shape must contain two positive integers")
        require_number("cell_size_m", self.cell_size_m, positive=True)
        require_point((self.x_min_m, self.y_max_m))
        if self.coordinate_frame != "local_metre":
            raise ValueError("P01 supports only local_metre; projected/rotated GIS needs P02")
        require_point((self.bounds[2], self.bounds[1]))
        if self.bounds[2] <= self.x_min_m or self.bounds[1] >= self.y_max_m:
            raise ValueError("grid extent is not representable at this coordinate precision")

    @property
    def bounds(self) -> tuple[float, float, float, float]:
        return (
            self.x_min_m,
            self.y_max_m - self.shape[0] * self.cell_size_m,
            self.x_min_m + self.shape[1] * self.cell_size_m,
            self.y_max_m,
        )

    def centre(self, row: int, col: int) -> PointXY:
        if not (0 <= row < self.shape[0] and 0 <= col < self.shape[1]):
            raise ValueError("cell is outside grid")
        return (
            self.x_min_m + (col + 0.5) * self.cell_size_m,
            self.y_max_m - (row + 0.5) * self.cell_size_m,
        )

    def cell_at(self, point: PointXY) -> tuple[int, int]:
        """Map points using west/north inclusive, east/south exclusive bounds."""
        require_point(point)
        x, y = point
        x_min, y_min, x_max, y_max = self.bounds
        if not (x_min <= x < x_max and y_min < y <= y_max):
            raise ValueError("point is outside grid's half-open bounds")
        return floor((y_max - y) / self.cell_size_m), floor((x - x_min) / self.cell_size_m)


@dataclass(frozen=True, slots=True)
class TreatmentProfile:
    """Independent treated ROS provider, never an implicit fuel-code multiplier.

    M0 is an ideal closed barrier. M1 integrates a separately supplied provider
    along covered edge portions. P01 retains source-cell ROS outside treatment.
    """

    profile_id: str
    mode: Literal["M0", "M1"]
    evidence: Evidence
    cost_units_per_m2: float
    rate_provider: DirectionalSpreadRateProvider | None = None

    def __post_init__(self) -> None:
        require_id("profile_id", self.profile_id)
        if not isinstance(self.evidence, Evidence):
            raise TypeError("evidence must be Evidence")
        require_number("cost_units_per_m2", self.cost_units_per_m2)
        if self.mode == "M0":
            if self.rate_provider is not None:
                raise ValueError("M0 must not supply a rate provider")
        elif self.mode == "M1":
            if not callable(getattr(self.rate_provider, "spread_rate_m_s", None)):
                raise ValueError("M1 requires an explicit directional treated ROS provider")
        else:
            raise ValueError("P01 supports M0/M1 only; spotting is unsupported")


@dataclass(frozen=True, slots=True)
class Treatment:
    """One straight, symmetric, flat-capped strip; width is in physical metres."""

    treatment_id: str
    start_m: PointXY
    end_m: PointXY
    width_m: float
    profile_id: str

    def __post_init__(self) -> None:
        require_id("treatment_id", self.treatment_id)
        require_id("profile_id", self.profile_id)
        require_point(self.start_m)
        require_point(self.end_m)
        if self.start_m == self.end_m:
            raise ValueError("treatment centreline must have positive length")
        require_number("width_m", self.width_m, positive=True)


@dataclass(frozen=True, slots=True)
class Plan:
    plan_id: str
    treatments: tuple[Treatment, ...] = ()

    def __post_init__(self) -> None:
        require_id("plan_id", self.plan_id)
        if not isinstance(self.treatments, tuple) or not all(
            isinstance(t, Treatment) for t in self.treatments
        ):
            raise TypeError("treatments must be a tuple of Treatment")
        ids = [t.treatment_id for t in self.treatments]
        if len(set(ids)) != len(ids):
            raise ValueError("treatment IDs must be unique")


@dataclass(frozen=True, slots=True)
class Ignition:
    point_m: PointXY
    time_s: float = 0.0

    def __post_init__(self) -> None:
        require_point(self.point_m)
        require_number("ignition time_s", self.time_s)


@dataclass(frozen=True, slots=True)
class Target:
    target_id: str
    point_m: PointXY
    weight: float = 1.0

    def __post_init__(self) -> None:
        require_id("target_id", self.target_id)
        require_point(self.point_m)
        require_number("target weight", self.weight, positive=True)


@dataclass(frozen=True, slots=True)
class Scenario:
    scenario_id: str
    ignitions: tuple[Ignition, ...]
    targets: tuple[Target, ...]
    horizon_s: float

    def __post_init__(self) -> None:
        require_id("scenario_id", self.scenario_id)
        for name, values, cls in (
            ("ignitions", self.ignitions, Ignition),
            ("targets", self.targets, Target),
        ):
            if (
                not isinstance(values, tuple)
                or not values
                or not all(isinstance(value, cls) for value in values)
            ):
                raise ValueError(f"{name} must be a nonempty typed tuple")
        if len({t.target_id for t in self.targets}) != len(self.targets):
            raise ValueError("target IDs must be unique")
        require_number("horizon_s", self.horizon_s, positive=True)
        if any(i.time_s > self.horizon_s for i in self.ignitions):
            raise ValueError("ignition must occur within the evaluation horizon")
