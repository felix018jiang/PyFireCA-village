"""Unobstructed finite-cylinder to finite-receiver radiation within one cell.

This is an explicit solid-flame geometry experiment, not a wildfire calibration.
Uniform diffuse emission integrates to the existing chi_rad * HRR budget. The
old inter-cell graph remains frozen; no atmospheric flow, reflections or new
inter-cell visibility are inferred. Only two-component cells are supported.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path

import numpy as np
import yaml
from numpy.polynomial.legendre import leggauss

from pyfireca.village.component_surfaces_v3 import DEFAULT_RECEIVER_SURFACE, ReceiverSurface
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.physics_v3 import V3CellPhysicsArrays

SCHEMA = "village-internal-radiation-1"


@dataclass(frozen=True, slots=True)
class InternalRadiationParameters:
    source_radius_fraction_cell: float
    atmospheric_extinction_m_inv: float
    quadrature_order: int
    max_quadrature_order: int
    relative_tolerance: float
    absolute_tolerance: float
    max_kernel_evaluations: int
    evidence: str
    provenance: str

    def __post_init__(self) -> None:
        for name in (
            "source_radius_fraction_cell",
            "atmospheric_extinction_m_inv",
            "relative_tolerance",
            "absolute_tolerance",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise TypeError(f"internal radiation {name} must be numeric")
            if not np.isfinite(value) or value < 0:
                raise ValueError(f"internal radiation {name} must be finite and nonnegative")
        if not 0 < self.source_radius_fraction_cell <= 0.5:
            raise ValueError("source radius fraction must lie in (0, 0.5]")
        if not 0 < self.relative_tolerance <= 0.1 or not 0 < self.absolute_tolerance <= 0.01:
            raise ValueError(
                "internal radiation tolerances must be positive and explicitly bounded"
            )
        for name in ("quadrature_order", "max_quadrature_order", "max_kernel_evaluations"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
                raise ValueError(f"internal radiation {name} must be a positive integer")
        if not 2 <= self.quadrature_order < self.max_quadrature_order <= 32:
            raise ValueError("quadrature orders must satisfy 2 <= initial < maximum <= 32")
        ratio = self.max_quadrature_order / self.quadrature_order
        if ratio < 2 or not float(np.log2(ratio)).is_integer():
            raise ValueError("maximum quadrature order must be initial times a power of two")
        if self.evidence not in {"synthetic", "literature_informed", "measured"}:
            raise ValueError("internal radiation evidence must be explicit")
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError("internal radiation provenance must be nonempty")

    def to_mapping(self) -> dict:
        return {"schema_version": SCHEMA, **asdict(self)}

    @classmethod
    def from_mapping(cls, value: dict) -> InternalRadiationParameters:
        if not isinstance(value, dict) or set(value) != {
            "schema_version",
            *cls.__dataclass_fields__,
        }:
            raise ValueError("internal radiation configuration requires exact parameter keys")
        if value["schema_version"] != SCHEMA:
            raise ValueError("unsupported internal radiation schema")
        return cls(**{key: value[key] for key in cls.__dataclass_fields__})


def load_internal_radiation_parameters(path: str | Path) -> InternalRadiationParameters:
    return InternalRadiationParameters.from_mapping(yaml.safe_load(Path(path).read_text()))


def _horizontal_patch(area_m2: float, z_m: float, order: int):
    """Two-sided square; weights sum to its ONE-face geometric area."""
    nodes, weights = leggauss(order)
    side = np.sqrt(area_m2)
    x, y = np.meshgrid(nodes * side / 2, nodes * side / 2, indexing="ij")
    points = np.column_stack((x.ravel(), y.ravel(), np.full(order * order, z_m)))
    normals = np.tile([0.0, 0.0, 1.0], (order * order, 1))
    areas = np.outer(weights, weights).ravel() * area_m2 / 4
    return points, normals, areas


def _cylinder(radius_m: float, bottom_m: float, length_m: float, order: int):
    """Closed convex envelope; lateral surface and both caps emit outward."""
    nodes, weights = leggauss(order)
    angles = (np.arange(4 * order) + 0.5) * (2 * np.pi / (4 * order))
    co, si = np.cos(angles), np.sin(angles)
    z = bottom_m + (nodes + 1) * length_m / 2
    points = [
        np.column_stack(
            (np.tile(radius_m * co, order), np.tile(radius_m * si, order), np.repeat(z, 4 * order))
        )
    ]
    normals = [
        np.column_stack((np.tile(co, order), np.tile(si, order), np.zeros(4 * order * order)))
    ]
    areas = [np.repeat(weights * length_m / 2, 4 * order) * radius_m * 2 * np.pi / (4 * order)]
    # Gauss integration in r^2 avoids a spurious radial area singularity.
    r = radius_m * np.sqrt((nodes + 1) / 2)
    cap_area = np.repeat(weights * radius_m**2 / 4, 4 * order) * 2 * np.pi / (4 * order)
    for height, direction in ((bottom_m, -1), (bottom_m + length_m, 1)):
        points.append(
            np.column_stack(
                (
                    np.repeat(r, 4 * order) * np.tile(co, order),
                    np.repeat(r, 4 * order) * np.tile(si, order),
                    np.full(4 * order * order, height),
                )
            )
        )
        normals.append(np.tile([0.0, 0.0, direction], (4 * order * order, 1)))
        areas.append(cap_area)
    return np.concatenate(points), np.concatenate(normals), np.concatenate(areas)


def _diffuse_view_factor(
    source, target, *, extinction_m_inv=0.0, target_two_sided=True, visibility=None
):
    """Double-area quadrature; no distance regularizer or budget normalization.

    Every entry is cos(theta_source) * cos(theta_target) / (pi * r^2)
    times emitting/receiving quadrature areas, divided by total source area.
    """
    sp, sn, sa = source
    tp, tn, ta = target
    total = 0.0
    # Bounded work arrays, independently of total quadrature pair count.
    for start in range(0, len(sp), 64):
        delta = tp[None, :, :] - sp[start : start + 64, None, :]
        distance2 = np.einsum("ijk,ijk->ij", delta, delta)
        if np.any(distance2 <= 0):
            raise ValueError("radiation surfaces have coincident quadrature points")
        distance = np.sqrt(distance2)
        direction = delta / distance[:, :, None]
        source_cos = np.maximum(0, np.einsum("ik,ijk->ij", sn[start : start + 64], direction))
        target_cos = -np.einsum("jk,ijk->ij", tn, direction)
        target_cos = np.abs(target_cos) if target_two_sided else np.maximum(0, target_cos)
        kernel = source_cos * target_cos / (np.pi * distance2)
        if visibility is not None:
            visible = visibility(sp[start : start + 64], tp)
            if visible.shape != kernel.shape or visible.dtype != np.bool_:
                raise TypeError("radiation visibility must return a boolean point-pair matrix")
            kernel *= visible
        if extinction_m_inv:
            kernel *= np.exp(-extinction_m_inv * distance)
        total += float(np.sum(kernel * sa[start : start + 64, None] * ta[None, :]))
    return total / float(sa.sum())


@dataclass(frozen=True, slots=True)
class InternalRadiationGraph:
    edges: ExposureEdges
    source_radius_m: np.ndarray
    source_surface_area_m2: np.ndarray
    quadrature_used_order: np.ndarray
    estimated_absolute_error: np.ndarray
    resource_upper_bound_evaluations: int
    evaluated_kernel_pairs: int
    geometry_cache_hits: int
    explicit_receiver_surfaces: bool = False

    def summary(self) -> dict:
        return {
            "internal_edge_count": len(self.edges.source),
            "max_used_order": int(self.quadrature_used_order.max(initial=0)),
            "max_estimated_absolute_error": float(self.estimated_absolute_error.max(initial=0)),
            "resource_upper_bound_evaluations": self.resource_upper_bound_evaluations,
            "evaluated_kernel_pairs": self.evaluated_kernel_pairs,
            "geometry_cache_hits": self.geometry_cache_hits,
            "error_estimate_is_rigorous_bound": False,
            "geometry": (
                "closed_uniform_diffuse_cylinder_to_explicit_rectangles"
                if self.explicit_receiver_surfaces
                else "closed_uniform_diffuse_cylinder_to_two_sided_horizontal_square"
            ),
            "intercell_geometry_rebuilt": False,
            "all_internal_integrals_converged": True,
        }


def build_internal_radiation_graph(
    grid: VillageGridSpec,
    cell_id: np.ndarray,
    node_burnable: np.ndarray,
    physics: V3CellPhysicsArrays,
    source_base_height_m: np.ndarray,
    source_footprint_fraction: np.ndarray,
    physical_coverage_fraction: np.ndarray,
    parameters: InternalRadiationParameters,
    receiver_surfaces: tuple[ReceiverSurface, ...] | None = None,
) -> InternalRadiationGraph:
    """Build same-cell edges only, rejecting overlapping or obstructed geometry.

    A two-component cell has no third receiving patch to obscure the exchange.
    Three-component cells require an explicit intervening-surface model and are
    rejected rather than silently treated as unobstructed.
    """
    ids = np.asarray(cell_id)
    mask = np.asarray(node_burnable)
    n = ids.size
    if receiver_surfaces is not None and (
        len(receiver_surfaces) != n
        or any(not isinstance(s, ReceiverSurface) for s in receiver_surfaces)
    ):
        raise ValueError("receiver surfaces must provide one ReceiverSurface per node")
    if not isinstance(parameters, InternalRadiationParameters):
        raise TypeError("parameters must be InternalRadiationParameters")
    if (
        n == 0
        or ids.ndim != 1
        or not np.issubdtype(ids.dtype, np.integer)
        or np.any(ids < 0)
        or np.any(ids >= grid.height * grid.width)
    ):
        raise ValueError("internal radiation cell ids must refer to the physical grid")
    if mask.shape != (n,) or mask.dtype != np.bool_:
        raise TypeError("internal radiation burnable mask must be a node boolean array")
    if physics.peak_hrr_W.shape != (1, n):
        raise ValueError("internal radiation node physics must use 1xN storage")
    values = []
    for name, value in (
        ("source bases", source_base_height_m),
        ("footprints", source_footprint_fraction),
        ("coverage", physical_coverage_fraction),
    ):
        value = np.asarray(value, dtype=np.float64)
        if value.shape != (n,) or np.any(~np.isfinite(value)) or np.any(value < 0):
            raise ValueError(f"internal radiation {name} must be finite nonnegative node arrays")
        values.append(value)
    bases, footprints, coverage = values
    if np.any(footprints > 1) or np.any(coverage > 1):
        raise ValueError("internal radiation coverage and footprint cannot exceed one")
    counts = np.bincount(ids[mask], minlength=grid.height * grid.width)
    if np.any(counts > 2):
        raise ValueError(
            "internal radiation needs intervening-surface visibility for three components"
        )
    orders = []
    order = parameters.quadrature_order
    while order <= parameters.max_quadrature_order:
        orders.append(order)
        order *= 2
    candidate_count = int(np.sum(counts * (counts - 1)))
    upper = candidate_count * sum(12 * order**4 for order in orders)
    if upper > parameters.max_kernel_evaluations:
        raise ValueError(
            f"RESOURCE_LIMIT: internal radiation kernel upper bound {upper} "
            f"exceeds {parameters.max_kernel_evaluations}; no quadrature executed"
        )
    radius = (
        grid.cell_size_m * parameters.source_radius_fraction_cell * np.sqrt(coverage * footprints)
    )
    lengths = physics.source_height_m.ravel()
    receivers = physics.receiver_height_m.ravel()
    area = physics.receiver_area_m2.ravel()
    if any(np.any(~np.isfinite(v[mask])) for v in (lengths, receivers, area)):
        raise ValueError("burnable radiation node geometry must be finite")
    if np.any(mask & ((radius <= 0) | (lengths <= 0) | (area <= 0) | (receivers <= 0))):
        raise ValueError(
            "burnable radiation nodes require positive radius, flame length and receiver area"
        )
    radius = np.where(mask, radius, 0.0)
    surface = 2 * np.pi * radius * (lengths + radius)
    groups = {}
    for node in np.flatnonzero(mask):
        groups.setdefault(int(ids[node]), []).append(int(node))
    sources, targets, weights, used, errors = [], [], [], [], []
    cache = {}
    actual, hits = 0, 0
    for cell, nodes in groups.items():
        for source in nodes:
            for target in nodes:
                if source == target:
                    continue
                receiver_surface = (
                    receiver_surfaces[target] if receiver_surfaces else DEFAULT_RECEIVER_SURFACE
                )
                overlap = (
                    receiver_surface.intersects_cylinder(
                        area[target],
                        receivers[target],
                        radius[source],
                        bases[source],
                        lengths[source],
                    )
                    if receiver_surfaces is not None
                    else bases[source] <= receivers[target] <= bases[source] + lengths[source]
                )
                if overlap:
                    raise ValueError(
                        f"RADIATION_GEOMETRY_OVERLAP: cell {cell} receiver node {target} "
                        f"intersects flame envelope of node {source}; "
                        "no singular-distance fix is applied"
                    )
                key = (
                    float(radius[source]),
                    float(lengths[source]),
                    float(receivers[target] - bases[source]),
                    float(area[target]),
                    receiver_surface,
                )
                if key in cache:
                    weight, final_order, error = cache[key]
                    hits += 1
                else:
                    previous = None
                    for order in orders:
                        emitter = _cylinder(key[0], 0.0, key[1], order)
                        receiver = (
                            _horizontal_patch(key[3], key[2], order)
                            if receiver_surface == DEFAULT_RECEIVER_SURFACE
                            else receiver_surface.quadrature(key[3], key[2], order)
                        )
                        weight = _diffuse_view_factor(
                            emitter,
                            receiver,
                            extinction_m_inv=parameters.atmospheric_extinction_m_inv,
                            target_two_sided=receiver_surface.two_sided,
                        )
                        actual += 12 * order**4
                        if not np.isfinite(weight) or weight < 0:
                            raise ValueError("radiation quadrature must be finite and nonnegative")
                        if previous is not None:
                            error = abs(weight - previous)
                            if (
                                error
                                <= parameters.absolute_tolerance
                                + parameters.relative_tolerance * abs(weight)
                            ):
                                break
                        previous = weight
                    else:
                        raise ValueError(
                            f"RADIATION_QUADRATURE_NOT_CONVERGED: cell {cell}, "
                            f"nodes {source}->{target}, final order {order}, "
                            f"last difference {error}; improve geometry or quadrature budget"
                        )
                    final_order = order
                    if weight > 1 + parameters.absolute_tolerance:
                        raise ValueError("receiver exceeds total emitted radiation")
                    if (
                        receiver_surface == DEFAULT_RECEIVER_SURFACE
                        and weight > 0.5 + parameters.absolute_tolerance
                    ):
                        raise ValueError(
                            "horizontal receiver exceeds the finite-cylinder half-space bound"
                        )
                    cache[key] = weight, final_order, error
                sources.append(source)
                targets.append(target)
                weights.append(weight)
                used.append(final_order)
                errors.append(error)
    edges = ExposureEdges(
        np.asarray(sources, dtype=np.int32),
        np.asarray(targets, dtype=np.int32),
        np.asarray(weights),
        n,
    )
    return InternalRadiationGraph(
        edges,
        radius,
        surface,
        np.asarray(used, dtype=np.int16),
        np.asarray(errors),
        upper,
        actual,
        hits,
        receiver_surfaces is not None,
    )


def combine_internal_and_frozen_edges(
    frozen: ExposureEdges, internal: ExposureEdges
) -> ExposureEdges:
    """Allocate extra paths only from previously unintercepted emitted energy."""
    if frozen.cell_count != internal.cell_count:
        raise ValueError("internal and frozen graphs must share node indexing")
    source = np.concatenate((frozen.source, internal.source))
    target = np.concatenate((frozen.target, internal.target))
    weight = np.concatenate((frozen.weight, internal.weight))
    outgoing = np.bincount(source, weights=weight, minlength=frozen.cell_count)
    if np.any(outgoing > 1 + 1e-12):
        offender = int(np.argmax(outgoing))
        raise ValueError(
            f"RADIATION_BUDGET_EXCEEDED: component {offender} allocates "
            f"{outgoing[offender]} of emitted radiation; no renormalization is applied"
        )
    return ExposureEdges(source, target, weight, frozen.cell_count)
