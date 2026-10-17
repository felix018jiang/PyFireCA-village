"""Observe annotated objects independently of the single-owner fuel raster.

Shared cells can be observed by multiple outlines but supply fuel only once.
They retain one model temperature: these observations do not resolve subcell
independent buildings or establish that nested annotations are distinct houses.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from math import ceil, floor

import numpy as np
import shapely
from shapely.geometry import Polygon
from shapely.ops import unary_union


def binding_identity(request):
    digest = hashlib.sha256(json.dumps(asdict(request.grid), sort_keys=True).encode())
    digest.update(np.ascontiguousarray(request.building_index).tobytes())
    return digest.hexdigest()


@dataclass(frozen=True, slots=True)
class ObjectObservation:
    object_id: int
    label: str
    source_label: str
    cell_ids: np.ndarray
    areas_m2: np.ndarray
    vector_area_m2: float


@dataclass(frozen=True, slots=True)
class ObjectObservations:
    objects: tuple[ObjectObservation, ...]
    grid_shape: tuple[int, int]
    binding_sha256: str
    sha256: str

    @property
    def ids(self):
        return tuple(o.object_id for o in self.objects)

    @property
    def types(self):
        return {o.object_id: o.source_label for o in self.objects}

    def validate_binding(self, request):
        if (
            request.grid.shape != self.grid_shape
            or binding_identity(request) != self.binding_sha256
        ):
            raise ValueError("object observer does not match the input building grid")

    def objects_at_cell(self, cell_id):
        return tuple(o.object_id for o in self.objects if np.any(o.cell_ids == cell_id))

    def first_ignitions(self, ignition_time_s):
        times = np.asarray(ignition_time_s)
        if times.shape != self.grid_shape or np.any(times[np.isfinite(times)] < 0):
            raise ValueError("invalid object ignition time array")
        first = []
        for o in self.objects:
            t = times.ravel()[o.cell_ids]
            valid = t[np.isfinite(t)]
            if len(valid):
                first.append(
                    {
                        "building_index": o.object_id,
                        "source_label": o.source_label,
                        "time_s": float(valid.min()),
                        "object_label": o.label,
                    }
                )
        return sorted(first, key=lambda row: (row["time_s"], row["building_index"]))

    def summary(self):
        counts = np.zeros(np.prod(self.grid_shape), dtype=int)
        for o in self.objects:
            counts[o.cell_ids] += 1
        missing = [o.label for o in self.objects if not len(o.cell_ids)]
        uncovered = [
            {
                "label": o.label,
                "uncovered_area_m2": max(0.0, o.vector_area_m2 - float(o.areas_m2.sum())),
            }
            for o in self.objects
            if o.vector_area_m2 - float(o.areas_m2.sum()) > 1e-6
        ]
        return {
            "object_count": len(self.objects),
            "unrepresented_labels": missing,
            "partly_unrepresented": uncovered,
            "shared_observation_cells": int((counts > 1).sum()),
            "observation_sha256": self.sha256,
            "fuel_duplicated": False,
            "subcell_independent_temperatures": False,
            "object_semantics": "original annotation objects; nested identities unresolved",
        }


def build_object_observations(request, structures, labels):
    """Intersect every outline with modeled building cells; retain all identities.

    Labels map the numeric evaluation identity to its annotation identifier.
    Missing and partly represented geometry are explicit and cannot pass silently.
    """
    ids = {value: int(key) for key, value in labels.items()}
    if len(ids) != len(labels) or len(structures) != len(ids):
        raise ValueError("annotation identities must be unique and match declared labels")
    objects = []
    digest = hashlib.sha256(binding_identity(request).encode())
    seen = set()
    grid = request.grid
    for row in sorted(structures, key=lambda r: r["building_id"]):
        label = row["building_id"]
        if label not in ids or label in seen:
            raise ValueError("unexpected or duplicate annotation identity")
        seen.add(label)
        kind = row["properties"]["source_label"]
        if kind not in {"building", "fengyuqiao"}:
            raise ValueError("unknown annotated structure type")
        polygon = unary_union([Polygon(p["exterior"], p["holes"]) for p in row["polygons"]])
        if not polygon.is_valid or polygon.is_empty or polygon.area <= 0:
            raise ValueError("invalid annotated building geometry")
        xmin, ymin, xmax, ymax = polygon.bounds
        c0 = max(0, floor((xmin - grid.x_min_m) / grid.cell_size_m))
        c1 = min(grid.width, ceil((xmax - grid.x_min_m) / grid.cell_size_m))
        r0 = max(0, floor((grid.y_max_m - ymax) / grid.cell_size_m))
        r1 = min(grid.height, ceil((grid.y_max_m - ymin) / grid.cell_size_m))
        if r1 <= r0 or c1 <= c0:
            cell_ids = np.array([], dtype=np.int64)
            areas = np.array([], dtype=float)
        else:
            rr, cc = np.mgrid[r0:r1, c0:c1]
            left = grid.x_min_m + cc * grid.cell_size_m
            top = grid.y_max_m - rr * grid.cell_size_m
            boxes = shapely.box(left, top - grid.cell_size_m, left + grid.cell_size_m, top)
            areas = shapely.area(shapely.intersection(boxes, polygon))
            keep = (areas > 1e-10) & (request.building_index[rr, cc] >= 0)
            cell_ids = np.asarray((rr * grid.width + cc)[keep], dtype=np.int64)
            areas = np.asarray(areas[keep], dtype=float)
        cell_ids.setflags(write=False)
        areas.setflags(write=False)
        digest.update(json.dumps([ids[label], label, kind], sort_keys=True).encode())
        digest.update(polygon.wkb)
        digest.update(cell_ids.tobytes())
        digest.update(areas.tobytes())
        objects.append(
            ObjectObservation(ids[label], label, kind, cell_ids, areas, float(polygon.area))
        )
    result = ObjectObservations(
        tuple(sorted(objects, key=lambda o: o.object_id)),
        grid.shape,
        binding_identity(request),
        digest.hexdigest(),
    )
    summary = result.summary()
    if summary["unrepresented_labels"] or summary["partly_unrepresented"]:
        raise ValueError(f"annotated geometry not fully represented in fuel raster: {summary}")
    return result
