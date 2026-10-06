"""Prescribed horizontal wind for W01; no terrain-flow or fire coupling.

Use parse_wind or load_wind to create a validated provider. Wind directions are
meteorological FROM angles relative to grid north; returned vectors point TO.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass
from math import cos, isfinite, radians, sin
from pathlib import Path
from typing import Any, Protocol

import yaml


def _number(value: Any, name: str, *, minimum: float | None = None) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a finite number, not a boolean or string")
    try:
        result = float(value)
    except OverflowError as exc:
        raise ValueError(f"{name} must be finite") from exc
    if not isfinite(result) or (minimum is not None and result < minimum):
        raise ValueError(f"{name} must be finite and >= {minimum}")
    return result


def _object(value: Any, keys: set[str], name: str) -> dict:
    if not isinstance(value, dict) or any(not isinstance(k, str) for k in value):
        raise ValueError(f"{name} must be a mapping with string keys")
    if value.keys() != keys:
        raise ValueError(
            f"{name}: missing keys {sorted(keys - value.keys())}; "
            f"unknown keys {sorted(value.keys() - keys)}"
        )
    return value


def _evidence(evidence: Any, source: Any, name: str) -> None:
    if evidence not in ("synthetic", "measured", "literature", "calibrated"):
        raise ValueError(f"{name}.evidence is not a supported evidence level")
    if not isinstance(source, str) or not source.strip():
        raise ValueError(f"{name}.source must be non-empty text")


def _quantity(raw: Any, unit: str, name: str) -> float:
    data = _object(raw, {"value", "unit", "evidence", "source"}, name)
    if data["unit"] != unit:
        raise ValueError(f"{name}.unit must be {unit!r}")
    _evidence(data["evidence"], data["source"], name)
    return _number(data["value"], name + ".value", minimum=0.0)


def wind_from_to_velocity(speed_m_s: float, from_deg: float) -> tuple[float, float]:
    """Return grid-east/grid-north m/s; north wind has negative north velocity."""
    speed = _number(speed_m_s, "speed_m_s", minimum=0.0)
    angle = _number(from_deg, "from_deg", minimum=0.0)
    if angle >= 360:
        raise ValueError("from_deg must lie in [0, 360)")
    # Exact cardinal zeros also make opposite cardinal winds cancel exactly.
    cardinal = {0.0: (0.0, -1.0), 90.0: (-1.0, 0.0), 180.0: (0.0, 1.0), 270.0: (1.0, 0.0)}
    east, north = cardinal.get(angle, (-sin(radians(angle)), -cos(radians(angle))))
    return speed * east, speed * north


class WindField(Protocol):
    """Shared point-query interface for radiation geometry and particle transport."""

    def velocity_at(
        self, time_s: float, *, x_m: float = 0.0, y_m: float = 0.0, z_m: float = 0.0
    ) -> tuple[float, float, float]: ...

    def next_change_after(self, time_s: float) -> float | None: ...


@dataclass(frozen=True, slots=True)
class PrescribedWind:
    """Immutable, spatially uniform field; construct with parse_wind/load_wind."""

    mode: str
    end_time_s: float
    reference_height_m: float | None
    _times: tuple[float, ...]
    _uv: tuple[tuple[float, float], ...]
    _configuration_json: str

    def to_dict(self) -> dict:
        """A detached evidence-preserving snapshot, safe for caller mutation."""
        return json.loads(self._configuration_json)

    @property
    def configuration_sha256(self) -> str:
        return hashlib.sha256(self._configuration_json.encode("utf-8")).hexdigest()

    def _time(self, value: float) -> float:
        time = _number(value, "time_s", minimum=0.0)
        if time > self.end_time_s:
            raise ValueError("time_s exceeds the validated simulation window")
        return time

    def velocity_at(
        self, time_s: float, *, x_m: float = 0.0, y_m: float = 0.0, z_m: float = 0.0
    ) -> tuple[float, float, float]:
        time = self._time(time_s)
        for name, value in (("x_m", x_m), ("y_m", y_m), ("z_m", z_m)):
            _number(value, name)
        if self.mode == "legacy_edges":
            raise ValueError("legacy_edges has no recoverable wind field; do not assume zero wind")
        if self.mode == "uniform":
            return *self._uv[0], 0.0
        right = bisect.bisect_left(self._times, time)
        if self._times[right] == time:
            return *self._uv[right], 0.0
        left = right - 1
        fraction = (time - self._times[left]) / (self._times[right] - self._times[left])
        east, north = (
            (1.0 - fraction) * self._uv[left][axis] + fraction * self._uv[right][axis]
            for axis in (0, 1)
        )
        return east, north, 0.0

    def next_change_after(self, time_s: float) -> float | None:
        """Next time-series knot within the validated window (strictly after t)."""
        time = self._time(time_s)
        if self.mode != "timeseries":
            return None
        index = bisect.bisect_right(self._times, time)
        if index < len(self._times) and self._times[index] <= self.end_time_s:
            return self._times[index]
        return None

    def horizontal_displacement(self, start_s: float, end_s: float) -> tuple[float, float]:
        """Exact integral of uniform-in-space, piecewise-linear wind, in metres.

        This is passive horizontal advection, not a firebrand trajectory model.
        """
        start, end = self._time(start_s), self._time(end_s)
        if end < start:
            raise ValueError("end_s must be >= start_s")
        self.velocity_at(start)  # Reject legacy even for a zero-length interval.
        times = [start, *(t for t in self._times if start < t < end), end]
        displacement = [0.0, 0.0]
        for a, b in zip(times[:-1], times[1:], strict=True):
            va, vb = self.velocity_at(a), self.velocity_at(b)
            for axis in (0, 1):
                displacement[axis] += (0.5 * va[axis] + 0.5 * vb[axis]) * (b - a)
        if not all(isfinite(value) for value in displacement):
            raise ValueError("wind displacement overflows; reduce speed or duration")
        return displacement[0], displacement[1]


def parse_wind(raw: Any, *, end_time_s: float) -> PrescribedWind:
    """Validate the exact W00 wind object; never infer weather from legacy edges."""
    end = _number(end_time_s, "end_time_s", minimum=0.0)
    if end == 0:
        raise ValueError("end_time_s must be positive")
    if not isinstance(raw, dict):
        raise ValueError("wind must be a mapping")
    mode = raw.get("mode")
    common = {"mode", "reference_height_m", "direction_reference"}
    if mode == "legacy_edges":
        _object(raw, {"mode"}, "wind")
        height, times, vectors = None, (), ()
    elif mode in ("uniform", "timeseries"):
        extra = (
            {"speed_m_s", "from_deg"} if mode == "uniform" else {"samples", "evidence", "source"}
        )
        _object(raw, common | extra, "wind")
        if raw["direction_reference"] != "grid_north":
            raise ValueError(
                "wind.direction_reference must be 'grid_north'; convert true north first"
            )
        height = _quantity(raw["reference_height_m"], "m", "wind.reference_height_m")
        if height == 0:
            raise ValueError("wind.reference_height_m must be positive")
        if mode == "uniform":
            speed = _quantity(raw["speed_m_s"], "m/s", "wind.speed_m_s")
            angle = _quantity(raw["from_deg"], "deg", "wind.from_deg")
            times, vectors = (0.0,), (wind_from_to_velocity(speed, angle),)
        else:
            _evidence(raw["evidence"], raw["source"], "wind")
            samples = raw["samples"]
            if not isinstance(samples, list) or len(samples) < 2:
                raise ValueError("wind.samples requires at least two samples")
            ts, uv = [], []
            for index, sample in enumerate(samples):
                name = f"wind.samples[{index}]"
                _object(sample, {"time_s", "speed_m_s", "from_deg"}, name)
                ts.append(_number(sample["time_s"], name + ".time_s", minimum=0.0))
                try:
                    uv.append(wind_from_to_velocity(sample["speed_m_s"], sample["from_deg"]))
                except ValueError as exc:
                    raise ValueError(f"{name}: {exc}") from exc
            if any(b <= a for a, b in zip(ts[:-1], ts[1:], strict=True)):
                raise ValueError("wind.samples times must be strictly increasing")
            if ts[0] != 0.0 or ts[-1] < end:
                raise ValueError("wind.samples must cover [0, end_time_s]; no extrapolation")
            times, vectors = tuple(ts), tuple(uv)
    else:
        raise ValueError(f"wind.mode {mode!r} is unsupported in W01 (grid_uv requires W05)")
    snapshot = json.dumps(
        raw, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
    return PrescribedWind(mode, end, height, times, vectors, snapshot)


class _UniqueKeyLoader(yaml.SafeLoader):
    """Reject duplicate YAML fields instead of silently replacing a wind setting."""


def _unique_mapping(loader: _UniqueKeyLoader, node: yaml.MappingNode) -> dict:
    result = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node)
        if not isinstance(key, str):
            raise ValueError(f"YAML line {key_node.start_mark.line + 1}: keys must be strings")
        if key in result:
            raise ValueError(f"YAML line {key_node.start_mark.line + 1}: duplicate key {key!r}")
        result[key] = loader.construct_object(value_node)
    return result


_UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _unique_mapping)


def load_wind(path: str | Path, *, end_time_s: float) -> PrescribedWind:
    """Load a standalone wind object; errors retain its original YAML path."""
    source = Path(path).resolve()
    try:
        raw = yaml.load(source.read_text(encoding="utf-8"), Loader=_UniqueKeyLoader)
        return parse_wind(raw, end_time_s=end_time_s)
    except (ValueError, yaml.YAMLError) as exc:
        raise ValueError(f"{source}: {exc}") from exc


def inspect_wind(path: str | Path, *, end_time_s: float) -> dict:
    """Compute a small diagnostic using the same provider intended for W02/W03."""
    wind = load_wind(path, end_time_s=end_time_s)
    times = sorted(
        {
            0.0,
            wind.end_time_s / 2,
            wind.end_time_s,
            *(t for t in wind._times if t <= wind.end_time_s),
        }
    )
    samples = []
    for time in times:
        east, north, vertical = wind.velocity_at(time)
        dx, dy = wind.horizontal_displacement(0.0, time)
        samples.append(
            {
                "time_s": time,
                "u_east_m_s": east,
                "v_north_m_s": north,
                "w_m_s": vertical,
                "advection_x_m": dx,
                "advection_y_m": dy,
            }
        )
    return {
        "schema_version": "village-wind-inspection-1",
        "wind": wind.to_dict(),
        "wind_configuration_sha256": wind.configuration_sha256,
        "end_time_s": wind.end_time_s,
        "fire_simulation_executed": False,
        "interpretation": "passive horizontal advection only; not firebrand landing or fire spread",
        "samples": samples,
    }
