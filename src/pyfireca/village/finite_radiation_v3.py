"""Opt-in complete component radiation graph with finite surfaces and visibility.

This replaces, rather than supplements, the inherited inter-cell graph. Static
opaque receiver rectangles and a stepwise DEM are explicit geometry hypotheses;
they do not resolve porous crowns, building interiors or dynamic flame shape.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
import yaml

from pyfireca.village.component_radiation_v3 import (
    InternalRadiationParameters,
)
from pyfireca.village.component_surfaces_v3 import DEFAULT_RECEIVER_SURFACE, ReceiverSurface
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.radiation_clipping_v3 import (
    clipped_view_factor,
    segmented_cylinder,
    source_height_planes,
    source_point_count,
    terrain_box_faces,
)
from pyfireca.village.radiation_visibility_v3 import coplanar_rectangles_overlap

SCHEMA = "village-finite-radiation-1"


@dataclass(frozen=True, slots=True)
class FiniteRadiationConfiguration:
    integration: InternalRadiationParameters
    max_center_distance_m: float
    receiver_visibility: bool
    terrain_visibility: bool
    max_visibility_tests: int
    evidence: str
    provenance: str
    max_receiver_triangles: int = 32
    max_source_height_segments: int = 32

    def __post_init__(self) -> None:
        if not isinstance(self.integration, InternalRadiationParameters):
            raise TypeError("finite radiation requires explicit integration parameters")
        distance = self.max_center_distance_m
        if isinstance(distance, bool) or not isinstance(distance, (int, float)):
            raise TypeError("finite radiation cutoff must be numeric")
        if not np.isfinite(distance) or distance <= 0:
            raise ValueError("finite radiation cutoff must be finite and positive")
        if not isinstance(self.receiver_visibility, bool) or not isinstance(
            self.terrain_visibility, bool
        ):
            raise TypeError("finite radiation visibility flags must be boolean")
        if (
            isinstance(self.max_visibility_tests, bool)
            or not isinstance(self.max_visibility_tests, int)
            or self.max_visibility_tests <= 0
        ):
            raise ValueError(
                "finite radiation visibility resource limit must be a positive integer"
            )
        if self.evidence not in {"synthetic", "literature_informed", "measured"}:
            raise ValueError("finite radiation evidence must be explicit")
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError("finite radiation provenance must be nonempty")
        if (
            isinstance(self.max_receiver_triangles, bool)
            or not isinstance(self.max_receiver_triangles, int)
            or not 3 <= self.max_receiver_triangles <= 128
        ):
            raise ValueError("receiver triangle limit must be an integer in [3, 128]")
        if (
            isinstance(self.max_source_height_segments, bool)
            or not isinstance(self.max_source_height_segments, int)
            or not 1 <= self.max_source_height_segments <= 128
        ):
            raise ValueError("source height segment limit must be an integer in [1, 128]")

    def to_mapping(self) -> dict:
        return {
            "schema_version": SCHEMA,
            **{
                key: getattr(self, key) for key in self.__dataclass_fields__ if key != "integration"
            },
            "integration": self.integration.to_mapping(),
        }

    @classmethod
    def from_mapping(cls, value: dict) -> FiniteRadiationConfiguration:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            *cls.__dataclass_fields__,
        }:
            raise ValueError("finite radiation configuration requires exact schema keys")
        if value["schema_version"] != SCHEMA:
            raise ValueError("unsupported finite radiation schema")
        return cls(
            **{
                key: InternalRadiationParameters.from_mapping(value[key])
                if key == "integration"
                else value[key]
                for key in cls.__dataclass_fields__
            }
        )


def load_finite_radiation_configuration(path: str | Path) -> FiniteRadiationConfiguration:
    return FiniteRadiationConfiguration.from_mapping(yaml.safe_load(Path(path).read_text()))


@dataclass(frozen=True, slots=True)
class FiniteRadiationGraph:
    edges: ExposureEdges
    source_radius_m: np.ndarray
    source_surface_area_m2: np.ndarray
    quadrature_used_order: np.ndarray
    estimated_absolute_error: np.ndarray
    kernel_upper_bound: int
    visibility_upper_bound: int
    evaluated_kernel_pairs: int
    evaluated_visibility_tests: int
    geometry_cache_hits: int

    def summary(self) -> dict:
        return {
            "edge_count": len(self.edges.source),
            "max_used_order": int(self.quadrature_used_order.max(initial=0)),
            "max_estimated_absolute_error": float(self.estimated_absolute_error.max(initial=0)),
            "kernel_upper_bound": self.kernel_upper_bound,
            "visibility_upper_bound": self.visibility_upper_bound,
            "evaluated_kernel_pairs": self.evaluated_kernel_pairs,
            "evaluated_visibility_tests": self.evaluated_visibility_tests,
            "geometry_cache_hits": self.geometry_cache_hits,
            "intercell_geometry_rebuilt": True,
            "error_estimate_is_rigorous_bound": False,
            "all_integrals_converged": True,
            "terrain_representation": "piecewise_constant_cell_height",
            "excluded_unknown_visibility_policy": "opaque_when_terrain_visibility_enabled",
            "receiver_occluders": "static_opaque_rectangles_excluding_emitter_and_intended_target",
            "source_budget_policy": "reject_overallocation_without_normalization",
            "integration_method": "receiver_shadow_clipping_and_segmented_source_height_quadrature",
            "visibility_work_unit": "polygon_vertex_half_plane_tests",
            "unallocated_radiation_includes": "terrain_back_faces_and_untracked_paths",
            "occluders_remain_static_after_burnout": True,
        }


def _signature(geometry):
    return tuple(float(v) for array in geometry[:4] for v in array) + geometry[4:]


def build_finite_radiation_graph(
    grid: VillageGridSpec,
    cell_id: np.ndarray,
    node_burnable: np.ndarray,
    source_mask: np.ndarray,
    target_mask: np.ndarray,
    physics: V3CellPhysicsArrays,
    source_base_height_m: np.ndarray,
    source_footprint_fraction: np.ndarray,
    physical_coverage_fraction: np.ndarray,
    configuration: FiniteRadiationConfiguration,
    *,
    receiver_surfaces: tuple[ReceiverSurface, ...] | None = None,
    ground_elevation_m: np.ndarray | None = None,
    domain_mask: np.ndarray,
    known_mask: np.ndarray,
    max_edges: int,
) -> FiniteRadiationGraph:
    """Build all supported same/neighbor-cell paths before any quadrature work."""
    ids = np.asarray(cell_id)
    n = ids.size
    if (
        n == 0
        or ids.ndim != 1
        or not np.issubdtype(ids.dtype, np.integer)
        or np.any((ids < 0) | (ids >= grid.height * grid.width))
    ):
        raise ValueError("finite radiation node ids must refer to physical cells")
    for name, mask in (
        ("burnable", node_burnable),
        ("source", source_mask),
        ("target", target_mask),
    ):
        if np.shape(mask) != (n,) or np.asarray(mask).dtype != np.bool_:
            raise TypeError(f"finite radiation {name} mask must be a node boolean array")
    for mask in (domain_mask, known_mask):
        if np.shape(mask) != grid.shape or np.asarray(mask).dtype != np.bool_:
            raise TypeError("finite radiation domain/known masks must match the physical grid")
    if physics.peak_hrr_W.shape != (1, n):
        raise ValueError("finite radiation physics requires 1xN node storage")
    if not isinstance(configuration, FiniteRadiationConfiguration):
        raise TypeError("finite radiation configuration must be explicit")
    if isinstance(max_edges, bool) or not isinstance(max_edges, int) or max_edges <= 0:
        raise ValueError("finite radiation edge limit must be a positive integer")
    surfaces = (
        receiver_surfaces if receiver_surfaces is not None else (DEFAULT_RECEIVER_SURFACE,) * n
    )
    if len(surfaces) != n or any(not isinstance(s, ReceiverSurface) for s in surfaces):
        raise ValueError("finite radiation needs one receiver surface per node")
    values = []
    for value in (source_base_height_m, source_footprint_fraction, physical_coverage_fraction):
        value = np.asarray(value, dtype=np.float64)
        if value.shape != (n,) or np.any(~np.isfinite(value)) or np.any(value < 0):
            raise ValueError(
                "finite radiation source geometry must be finite nonnegative node arrays"
            )
        values.append(value)
    bases, footprints, coverage = values
    if np.any(footprints > 1) or np.any(coverage > 1):
        raise ValueError("finite radiation coverage/footprint cannot exceed one")
    usable = domain_mask & known_mask
    ground = (
        np.zeros(grid.shape)
        if ground_elevation_m is None
        else np.asarray(ground_elevation_m, dtype=np.float64)
    )
    if ground.shape != grid.shape or np.any(~np.isfinite(ground[usable])):
        raise ValueError("finite radiation ground must be finite on known included cells")
    ground = np.where(usable, ground, 0.0)
    ground -= float(ground[usable].min()) if usable.any() else 0.0
    if np.any(node_burnable & ~usable.ravel()[ids]):
        raise ValueError("finite radiation combustible nodes must be known included cells")
    p = configuration.integration
    radius = grid.cell_size_m * p.source_radius_fraction_cell * np.sqrt(coverage * footprints)
    lengths, areas, heights = (
        getattr(physics, field).ravel()
        for field in ("source_height_m", "receiver_area_m2", "receiver_height_m")
    )
    for values in (radius, lengths, areas, heights):
        if np.any(node_burnable & (~np.isfinite(values) | (values <= 0))):
            raise ValueError("finite radiation requires positive finite combustible geometry")
    radius = np.where(node_burnable, radius, 0.0)
    source_area = 2 * np.pi * radius * (lengths + radius)
    rows, cols = np.divmod(ids, grid.width)
    x, y = (cols + 0.5) * grid.cell_size_m, (grid.height - rows - 0.5) * grid.cell_size_m
    zbase = ground.ravel()[ids] + bases
    zreceiver = ground.ravel()[ids] + heights
    patches, bounds = {}, {}
    for node in np.flatnonzero(node_burnable):
        surface = surfaces[node]
        surface.validate_bounds(areas[node], heights[node], grid.cell_size_m)
        center, u, v, normal, width, height = surface.geometry(areas[node], zreceiver[node])
        center[:2] += [x[node], y[node]]
        patches[node] = center, u, v, normal, width, height
        extent = np.abs(u) * width / 2 + np.abs(v) * height / 2
        bounds[node] = center - extent, center + extent
    patches_by_cell = {}
    for node in patches:
        patches_by_cell.setdefault(int(ids[node]), []).append(int(node))
    if configuration.receiver_visibility:
        for a in patches:
            nearby = []
            for row in range(max(0, rows[a] - 1), min(grid.height, rows[a] + 2)):
                for col in range(max(0, cols[a] - 1), min(grid.width, cols[a] + 2)):
                    nearby.extend(patches_by_cell.get(row * grid.width + col, ()))
            for b in sorted(nearby):
                if b <= a:
                    continue
                if np.any(bounds[a][1] < bounds[b][0]) or np.any(bounds[a][0] > bounds[b][1]):
                    continue
                if coplanar_rectangles_overlap(patches[a], patches[b]):
                    raise ValueError(
                        f"RADIATION_RECEIVER_OVERLAP: nodes {a}/{b} share positive opaque area"
                    )
    targets_by_cell = {}
    for target in np.flatnonzero(node_burnable & target_mask):
        targets_by_cell.setdefault(int(ids[target]), []).append(int(target))
    # Bound offset traversal by the finite grid even for a very large cutoff.
    reach = int(
        np.ceil(min(configuration.max_center_distance_m / grid.cell_size_m, max(grid.shape)))
    )
    candidate_pairs = []
    for source in np.flatnonzero(node_burnable & source_mask):
        for row in range(max(0, rows[source] - reach), min(grid.height, rows[source] + reach + 1)):
            for col in range(
                max(0, cols[source] - reach), min(grid.width, cols[source] + reach + 1)
            ):
                if (
                    np.hypot(row - rows[source], col - cols[source]) * grid.cell_size_m
                    > configuration.max_center_distance_m + 1e-12
                ):
                    continue
                for target in targets_by_cell.get(row * grid.width + col, ()):
                    if source == target:
                        continue
                    candidate_pairs.append((int(source), target))
                    if len(candidate_pairs) > max_edges:
                        raise ValueError(
                            "RESOURCE_LIMIT: finite radiation edge limit exceeded before quadrature"
                        )
    orders = []
    order = p.quadrature_order
    while order <= p.max_quadrature_order:
        orders.append(order)
        order *= 2
    per_edge = sum(12 * order**4 for order in orders) * configuration.max_receiver_triangles
    kernel_upper = len(candidate_pairs) * per_edge
    if kernel_upper > p.max_kernel_evaluations:
        raise ValueError(
            f"RESOURCE_LIMIT: finite radiation kernel upper bound {kernel_upper} "
            f"exceeds {p.max_kernel_evaluations}; no quadrature executed"
        )
    candidates, visibility_upper = [], 0
    kernel_upper = 0
    for source, target in candidate_pairs:
        origin = np.array([x[source], y[source], zbase[source]])
        surface = replace(
            surfaces[target],
            offset_x_m=patches[target][0][0] - x[source],
            offset_y_m=patches[target][0][1] - y[source],
        )
        z = float(zreceiver[target] - zbase[source])
        if surface.intersects_cylinder(areas[target], z, radius[source], 0.0, lengths[source]):
            raise ValueError(
                f"RADIATION_GEOMETRY_OVERLAP: nodes {source}->{target}; no regularization applied"
            )
        source_low = origin + np.array([-radius[source], -radius[source], 0])
        source_high = origin + np.array([radius[source], radius[source], lengths[source]])
        low, high = (
            np.minimum(source_low, bounds[target][0]),
            np.maximum(source_high, bounds[target][1]),
        )
        blockers = []
        if configuration.receiver_visibility:
            nearby = []
            min_col = max(0, int(np.floor(low[0] / grid.cell_size_m)) - 1)
            max_col = min(grid.width - 1, int(np.floor(high[0] / grid.cell_size_m)) + 1)
            min_y = max(0, int(np.floor(low[1] / grid.cell_size_m)) - 1)
            max_y = min(grid.height - 1, int(np.floor(high[1] / grid.cell_size_m)) + 1)
            for yy in range(min_y, max_y + 1):
                row = grid.height - yy - 1
                for col in range(min_col, max_col + 1):
                    nearby.extend(patches_by_cell.get(row * grid.width + col, ()))
            for node in sorted(nearby):
                geometry = patches[node]
                if (
                    node in (source, target)
                    or np.any(bounds[node][1] < low)
                    or np.any(bounds[node][0] > high)
                ):
                    continue
                blockers.append((geometry[0] - origin, *geometry[1:]))
        terrain = []
        if configuration.terrain_visibility:
            min_col, max_col = (
                max(0, int(np.floor(low[0] / grid.cell_size_m))),
                min(grid.width - 1, int(np.floor(high[0] / grid.cell_size_m))),
            )
            min_y, max_y = (
                max(0, int(np.floor(low[1] / grid.cell_size_m))),
                min(grid.height - 1, int(np.floor(high[1] / grid.cell_size_m))),
            )
            for yy in range(min_y, max_y + 1):
                for col in range(min_col, max_col + 1):
                    row = grid.height - yy - 1
                    terrain.append(
                        (
                            col * grid.cell_size_m - x[source],
                            (col + 1) * grid.cell_size_m - x[source],
                            yy * grid.cell_size_m - y[source],
                            (yy + 1) * grid.cell_size_m - y[source],
                            float(ground[row, col] - zbase[source]),
                            not bool(usable[row, col]),
                        )
                    )
        min_z, max_z = float(low[2] - origin[2]), float(high[2] - origin[2])
        for cell in terrain:
            blockers.extend(terrain_box_faces(cell, min_z, max_z))
        receiver_geometry = surface.geometry(areas[target], z)
        height_planes = source_height_planes(receiver_geometry, blockers)
        counts = [
            source_point_count(
                radius[source],
                lengths[source],
                order,
                height_planes,
                configuration.max_source_height_segments,
            )
            for order in orders
        ]
        points_upper = sum(counts)
        kernel_upper += (
            sum(count * order**2 for count, order in zip(counts, orders, strict=True))
            * configuration.max_receiver_triangles
        )
        if kernel_upper > p.max_kernel_evaluations:
            raise ValueError(
                f"RESOURCE_LIMIT: segmented finite radiation kernel upper bound {kernel_upper} "
                f"exceeds {p.max_kernel_evaluations}; no quadrature executed"
            )
        visibility_upper += points_upper * (
            4 + 100 * configuration.max_receiver_triangles * len(blockers)
        )
        if visibility_upper > configuration.max_visibility_tests:
            raise ValueError(
                "RESOURCE_LIMIT: finite radiation visibility upper bound exceeds "
                f"{configuration.max_visibility_tests}; no quadrature executed"
            )
        key = (
            float(radius[source]),
            float(lengths[source]),
            float(areas[target]),
            z,
            surface,
            tuple(_signature(g) for g in blockers),
        )
        candidates.append((source, target, surface, z, blockers, height_planes, key))
    sources, targets, weights, used, errors = [], [], [], [], []
    cache = {}
    evaluations, visibility_tests, hits = 0, 0, 0
    for source, target, surface, z, blockers, height_planes, key in candidates:
        if key in cache:
            weight, final_order, error = cache[key]
            hits += 1
        else:
            previous = None
            for order in orders:
                emitter = segmented_cylinder(
                    radius[source],
                    lengths[source],
                    order,
                    height_planes,
                    configuration.max_source_height_segments,
                )
                receiver = surface.geometry(areas[target], z)
                weight, kernel_work, clipping_work = clipped_view_factor(
                    emitter,
                    receiver,
                    blockers,
                    order=order,
                    max_triangles=configuration.max_receiver_triangles,
                    extinction_m_inv=p.atmospheric_extinction_m_inv,
                    target_two_sided=surface.two_sided,
                )
                evaluations += kernel_work
                visibility_tests += clipping_work
                if not np.isfinite(weight) or weight < 0 or weight > 1 + p.absolute_tolerance:
                    raise ValueError("finite radiation quadrature is outside emitted-energy bounds")
                if previous is not None:
                    error = abs(weight - previous)
                    if error <= p.absolute_tolerance + p.relative_tolerance * abs(weight):
                        break
                previous = weight
            else:
                raise ValueError(
                    f"RADIATION_QUADRATURE_NOT_CONVERGED: nodes {source}->{target}, "
                    f"order {order}, last difference {error}"
                )
            final_order = order
            cache[key] = weight, final_order, error
        sources.append(source)
        targets.append(target)
        weights.append(weight)
        used.append(final_order)
        errors.append(error)
    outgoing = np.bincount(sources, weights=weights, minlength=n)
    if np.any(outgoing > 1 + 1e-12):
        raise ValueError(
            f"RADIATION_BUDGET_EXCEEDED: finite surfaces allocate {outgoing.max()} "
            "of emitted energy; no normalization applied"
        )
    return FiniteRadiationGraph(
        ExposureEdges(
            np.asarray(sources, dtype=np.int32),
            np.asarray(targets, dtype=np.int32),
            np.asarray(weights),
            n,
        ),
        radius,
        source_area,
        np.asarray(used, dtype=np.int16),
        np.asarray(errors),
        kernel_upper,
        visibility_upper,
        evaluations,
        visibility_tests,
        hits,
    )
