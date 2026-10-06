"""Explicit receiver rectangles for the opt-in same-cell radiation experiment.

Geometry is a scenario input, never inferred from RGB. Areas and heights remain
those of the resolved fuel component; specifying orientation creates no fuel.
Local x/y follow the metric grid coordinates (x east, y north for a north-up
grid), not pixel row directions or compass-bearing angle conventions.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import yaml
from numpy.polynomial.legendre import leggauss

from pyfireca.village.vegetation_prepare import LANDCOVER_IDS

SCHEMA = "village-receiver-surfaces-1"


@dataclass(frozen=True, slots=True)
class ReceiverSurface:
    orientation: str = "horizontal"
    azimuth_deg: float = 0.0
    offset_x_m: float = 0.0
    offset_y_m: float = 0.0
    aspect_ratio: float = 1.0
    two_sided: bool = True

    def __post_init__(self) -> None:
        if self.orientation not in {"horizontal", "vertical"}:
            raise ValueError("receiver orientation must be horizontal or vertical")
        if not isinstance(self.two_sided, bool):
            raise TypeError("receiver two_sided must be boolean")
        for key in ("azimuth_deg", "offset_x_m", "offset_y_m", "aspect_ratio"):
            value = getattr(self, key)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"receiver {key} must be numeric")
            if not np.isfinite(value):
                raise ValueError(f"receiver {key} must be finite")
        if not 0 <= self.azimuth_deg < 360:
            raise ValueError("receiver azimuth must be in [0, 360)")
        if self.aspect_ratio <= 0:
            raise ValueError("receiver aspect ratio must be positive")

    def geometry(self, area_m2: float, height_m: float):
        if not np.isfinite(area_m2) or area_m2 <= 0 or not np.isfinite(height_m):
            raise ValueError("receiver area must be positive and height finite")
        width = np.sqrt(area_m2) * np.sqrt(self.aspect_ratio)
        height = np.sqrt(area_m2) / np.sqrt(self.aspect_ratio)
        if not np.isfinite(width) or not np.isfinite(height):
            raise ValueError("receiver dimensions overflow")
        angle = np.deg2rad(self.azimuth_deg)
        c, s = np.cos(angle), np.sin(angle)
        if self.orientation == "horizontal":
            u, v, normal = np.array([c, s, 0]), np.array([-s, c, 0]), np.array([0, 0, 1])
        else:
            u, v, normal = np.array([-s, c, 0]), np.array([0, 0, 1]), np.array([c, s, 0])
        center = np.array([self.offset_x_m, self.offset_y_m, height_m])
        return center, u, v, normal, float(width), float(height)

    def validate_bounds(self, area_m2: float, height_m: float, cell_size_m: float) -> None:
        center, u, v, _, width, height = self.geometry(area_m2, height_m)
        extent = np.abs(u) * width / 2 + np.abs(v) * height / 2
        if np.any(np.abs(center[:2]) + extent[:2] > cell_size_m / 2 + 1e-12):
            raise ValueError("receiver rectangle extends outside its physical cell")
        if center[2] - extent[2] < -1e-12:
            raise ValueError("receiver rectangle extends below local ground")

    def intersects_cylinder(
        self, area_m2: float, height_m: float, radius_m: float, bottom_m: float, length_m: float
    ) -> bool:
        """Continuous rectangle/envelope test, including edges and tangency."""
        center, u, v, _, width, height = self.geometry(area_m2, height_m)
        if self.orientation == "horizontal":
            if not bottom_m - 1e-12 <= height_m <= bottom_m + length_m + 1e-12:
                return False
            planar = np.array([np.dot(center, u), np.dot(center, v)])
            closest = np.maximum(np.abs(planar) - np.array([width, height]) / 2, 0)
            distance = np.linalg.norm(closest)
        else:
            if height_m + height / 2 < bottom_m - 1e-12:
                return False
            if height_m - height / 2 > bottom_m + length_m + 1e-12:
                return False
            shift = np.clip(-np.dot(center[:2], u[:2]), -width / 2, width / 2)
            distance = np.linalg.norm(center[:2] + shift * u[:2])
        return bool(distance <= radius_m + 1e-12)

    def quadrature(self, area_m2: float, height_m: float, order: int):
        center, u, v, normal, width, height = self.geometry(area_m2, height_m)
        nodes, weights = leggauss(order)
        a, b = np.meshgrid(nodes * width / 2, nodes * height / 2, indexing="ij")
        points = center + a.ravel()[:, None] * u + b.ravel()[:, None] * v
        normals = np.tile(normal, (order * order, 1))
        areas = np.outer(weights, weights).ravel() * area_m2 / 4
        return points, normals, areas


DEFAULT_RECEIVER_SURFACE = ReceiverSurface()


@dataclass(frozen=True, slots=True)
class ReceiverSurfaceConfiguration:
    evidence: str
    provenance: str
    surfaces: dict[str, dict[str, ReceiverSurface]]

    def __post_init__(self) -> None:
        if self.evidence not in {"synthetic", "literature_informed", "measured"}:
            raise ValueError("receiver surface evidence must be explicit")
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError("receiver surface provenance must be nonempty")
        if not isinstance(self.surfaces, dict) or not self.surfaces:
            raise ValueError("receiver surfaces must be a nonempty mapping")
        clean = {}
        for label, parts in self.surfaces.items():
            if label not in LANDCOVER_IDS[1:] or not isinstance(parts, dict) or not parts:
                raise ValueError("receiver surfaces require burnable landcover/component mappings")
            for name, surface in parts.items():
                if not isinstance(name, str) or not name or not name.isascii():
                    raise ValueError("receiver component id must be nonempty ASCII")
                if not isinstance(surface, ReceiverSurface):
                    raise TypeError("receiver surfaces must contain ReceiverSurface values")
            clean[label] = dict(parts)
        object.__setattr__(self, "surfaces", clean)

    def validate_component_ids(self, recipes: dict[str, tuple[str, ...]]) -> None:
        for label, parts in self.surfaces.items():
            allowed = recipes.get(label, ("whole",))
            for name in parts:
                if name not in allowed:
                    raise ValueError(
                        f"unknown receiver component {label}/{name}; expected {allowed}"
                    )

    def to_mapping(self) -> dict:
        return {
            "schema_version": SCHEMA,
            "evidence": self.evidence,
            "provenance": self.provenance,
            "surfaces": {
                label: {name: asdict(surface) for name, surface in parts.items()}
                for label, parts in self.surfaces.items()
            },
        }

    @classmethod
    def from_mapping(cls, value: dict) -> ReceiverSurfaceConfiguration:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            *cls.__dataclass_fields__,
        }:
            raise ValueError("receiver surface configuration requires exact schema keys")
        if value["schema_version"] != SCHEMA or not isinstance(value["surfaces"], dict):
            raise ValueError("invalid receiver surface schema or surfaces")
        surfaces = {}
        for label, parts in value["surfaces"].items():
            if not isinstance(parts, dict):
                raise ValueError("receiver components must be a mapping")
            surfaces[label] = {}
            for name, fields in parts.items():
                if not isinstance(fields, dict) or set(fields) != set(
                    ReceiverSurface.__dataclass_fields__
                ):
                    raise ValueError("receiver rectangle requires exact geometry keys")
                surfaces[label][name] = ReceiverSurface(**fields)
        return cls(value["evidence"], value["provenance"], surfaces)


def load_receiver_surface_configuration(path: str | Path) -> ReceiverSurfaceConfiguration:
    return ReceiverSurfaceConfiguration.from_mapping(yaml.safe_load(Path(path).read_text()))
