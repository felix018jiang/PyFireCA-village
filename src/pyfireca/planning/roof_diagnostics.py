"""Counterfactual mechanism controls, deliberately outside protection evaluation.

These two-object experiments omit the village background and target feedback.
They inherit physical arrays exactly; switches remove transport/ignition paths,
never fit material parameters. They must not be interpreted as safe demolition.
"""

from dataclasses import dataclass, replace
from math import atan, cos, pi, radians, sin

import numpy as np
from shapely.geometry import box

from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.solver_v3 import V3SimulationRequest


@dataclass(frozen=True)
class DiagnosticRoof:
    """One unambiguous roof extracted from an already resolved experiment."""

    parameters: V3CellPhysicsArrays
    arrays: dict
    xy_m: np.ndarray
    elevation_m: np.ndarray
    geometry: tuple
    seed: np.ndarray
    neighbors: np.ndarray
    geometry_fraction: np.ndarray
    storage_cell_size_m: float

    @property
    def count(self):
        return len(self.xy_m)


def extract_roof(
    request, components, identifier, neighbors, geometry_fraction, *, parent_elevation_m
):
    """Keep singleton owners only, remapping the saved physical contact graph."""
    from shapely.geometry import shape

    selected = [p for p in components if tuple(p["building_ids"]) == (identifier,)]
    ids = np.asarray([p["node"] for p in selected], dtype=int)
    if not len(ids):
        raise ValueError("no unambiguous roof components")
    reverse = np.full(request.grid.width * request.grid.height, -1, dtype=int)
    reverse[ids] = np.arange(len(ids))
    old = neighbors[ids]
    mapped = reverse[np.maximum(old, 0)]
    mapped[old < 0] = -1
    fractions = geometry_fraction[ids].copy()
    fractions[mapped < 0] = 0
    arrays = {
        key: getattr(request, key).ravel()[ids].reshape(1, -1).copy()
        for key in (
            "domain_mask",
            "known_mask",
            "burnable_mask",
            "source_mask",
            "target_mask",
            "piloted_ignition_mask",
            "material_index",
            "landcover_index",
            "building_index",
        )
    }
    if not np.all(arrays["burnable_mask"]):
        raise ValueError("diagnostic extraction requires combustible roof")
    return DiagnosticRoof(
        V3CellPhysicsArrays(
            **{
                key: getattr(request.parameters, key).ravel()[ids].reshape(1, -1).copy()
                for key in V3CellPhysicsArrays.__dataclass_fields__
            }
        ),
        arrays,
        np.asarray([p["xy_m"] for p in selected]),
        np.zeros(len(ids))
        if parent_elevation_m is None
        else np.asarray(parent_elevation_m)
        .ravel()[[p["physical_cell_id"] for p in selected]]
        .copy(),
        tuple(shape(p["geometry"]) for p in selected),
        np.asarray([p.get("prescribed_ignition_source_id") == identifier for p in selected]),
        mapped.astype(np.int32),
        fractions,
        request.grid.cell_size_m,
    )


def kernel(source_xy, source_z, target_xy, target_z, source_height, transport):
    """Same transparent, regularized point equation as roof_radiation_graph."""
    tilt = atan(transport.wind_speed_m_s * transport.flame_tilt_coefficient_s_m)
    azimuth = radians((transport.wind_from_deg + 180) % 360)
    half = source_height / 2
    delta = target_xy[None, :, :] - source_xy[:, None, :]
    horizontal2 = (delta**2).sum(axis=2)
    delta[:, :, 0] -= (half * sin(tilt) * sin(azimuth))[:, None]
    delta[:, :, 1] -= (half * sin(tilt) * cos(azimuth))[:, None]
    dz = target_z[None, :] - (source_z + half * cos(tilt))[:, None]
    distance2 = (delta**2).sum(axis=2) + dz**2
    value = np.exp(-transport.atmospheric_extinction_m_inv * np.sqrt(distance2))
    value /= 4 * pi * (distance2 + transport.near_field_regularization_m**2)
    value[horizontal2 > transport.exposure_radius_m**2] = 0
    return value


def point_edges(source, target, transport, *, internal_radiation, seed_only):
    count, sources, targets, weights = source.count + target.count, [], [], []
    ids = np.flatnonzero(source.seed) if seed_only else np.arange(source.count)
    for start in range(0, len(ids), 128):
        take = ids[start : start + 128]
        k = kernel(
            source.xy_m[take],
            source.elevation_m[take],
            target.xy_m,
            target.elevation_m + target.parameters.receiver_height_m.ravel(),
            source.parameters.source_height_m.ravel()[take],
            transport,
        )
        s, t = np.nonzero(k)
        sources.extend(take[s])
        targets.extend(source.count + t)
        weights.extend(k[s, t] * target.parameters.receiver_area_m2.ravel()[t])
        if internal_radiation:
            k = kernel(
                source.xy_m[take],
                source.elevation_m[take],
                source.xy_m,
                source.elevation_m + source.parameters.receiver_height_m.ravel(),
                source.parameters.source_height_m.ravel()[take],
                transport,
            )
            k[np.arange(len(take)), take] = 0
            s, t = np.nonzero(k)
            sources.extend(take[s])
            targets.extend(t)
            weights.extend(k[s, t] * source.parameters.receiver_area_m2.ravel()[t])
    return ExposureEdges(
        np.asarray(sources, np.int32), np.asarray(targets, np.int32), np.asarray(weights), count
    )


def mechanism_request(source, target, scenario, transport, contact_parameters, *, mode):
    """No target-to-source, target internal, other-building or ground pathways."""
    if mode not in {"seed_only", "source_radiation", "source_contact", "source_both"}:
        raise ValueError("unknown mechanism control")
    seed_only = mode == "seed_only"
    if not source.seed.any():
        raise ValueError("fixed source footprint has no seeded components")
    count = source.count + target.count
    physics = V3CellPhysicsArrays(
        **{
            name: np.concatenate(
                [getattr(r.parameters, name).ravel() for r in (source, target)]
            ).reshape(1, -1)
            for name in V3CellPhysicsArrays.__dataclass_fields__
        }
    )
    arrays = {
        name: np.concatenate([r.arrays[name].ravel() for r in (source, target)]).reshape(1, -1)
        for name in source.arrays
    }
    if seed_only:
        arrays["piloted_ignition_mask"][0, : source.count] = source.seed
    request = V3SimulationRequest(
        grid=VillageGridSpec(1, count, source.storage_cell_size_m, 0, source.storage_cell_size_m),
        parameters=physics,
        exposure_edges=point_edges(
            source,
            target,
            transport,
            internal_radiation=mode in {"source_radiation", "source_both"},
            seed_only=seed_only,
        ),
        scenario=replace(
            scenario,
            ignitions=tuple(
                VillageIgnitionEvent(0.0, 0, int(i)) for i in np.flatnonzero(source.seed)
            ),
        ),
        sample_interval_s=18.0,
        ground_elevation_m=np.concatenate([source.elevation_m, target.elevation_m]).reshape(1, -1),
        **arrays,
    )
    contact = None
    if mode in {"source_contact", "source_both"}:
        if contact_parameters is None:
            raise ValueError("source contact diagnostic requires saved contact parameters")
        width = source.neighbors.shape[1]
        neighbors = np.full((count, width), -1, np.int32)
        fractions = np.zeros((count, width))
        neighbors[: source.count] = source.neighbors
        fractions[: source.count] = source.geometry_fraction
        contact = FlameContactOperator(neighbors, fractions, contact_parameters, physics)
    return request, contact


def quadrature_samples(roof, spacing_m, *, seed_only):
    """Area weights partition the frozen polygons; intensive values stay inherited."""
    if not np.isfinite(spacing_m) or spacing_m <= 0:
        raise ValueError("positive quadrature spacing required")
    xy, areas, parents = [], [], []
    for node, p in enumerate(roof.geometry):
        if seed_only and not roof.seed[node]:
            continue
        xmin, ymin, xmax, ymax = p.bounds
        for ix in range(int(np.floor(xmin / spacing_m)), int(np.ceil(xmax / spacing_m))):
            for iy in range(int(np.floor(ymin / spacing_m)), int(np.ceil(ymax / spacing_m))):
                part = p.intersection(
                    box(ix * spacing_m, iy * spacing_m, (ix + 1) * spacing_m, (iy + 1) * spacing_m)
                )
                if part.area <= 0:
                    continue
                # Centroid is an integration point, not a new ignition location.
                # Nonconvex cut pieces may put it outside; they carry area only.
                xy.append((part.centroid.x, part.centroid.y))
                areas.append(part.area / p.area * roof.parameters.covered_area_m2.ravel()[node])
                parents.append(node)
    parents = np.asarray(parents, int)
    return {
        "xy_m": np.asarray(xy),
        "area_m2": np.asarray(areas),
        "elevation_m": roof.elevation_m[parents],
        "source_height_m": roof.parameters.source_height_m.ravel()[parents],
        "receiver_height_m": roof.parameters.receiver_height_m.ravel()[parents],
    }


def uniform_flux(source_samples, target_samples, transport):
    """Flux for unit radiative emission density (1 W/m²), no thermal response."""
    flux = np.zeros(len(target_samples["xy_m"]))
    for start in range(0, len(source_samples["xy_m"]), 64):
        take = slice(start, start + 64)
        flux += source_samples["area_m2"][take] @ kernel(
            source_samples["xy_m"][take],
            source_samples["elevation_m"][take],
            target_samples["xy_m"],
            target_samples["elevation_m"] + target_samples["receiver_height_m"],
            source_samples["source_height_m"][take],
            transport,
        )
    return flux


def point_samples(roof, *, seed_only=False):
    take = roof.seed if seed_only else np.ones(roof.count, bool)
    return {
        "xy_m": roof.xy_m[take],
        "area_m2": roof.parameters.covered_area_m2.ravel()[take],
        "elevation_m": roof.elevation_m[take],
        "source_height_m": roof.parameters.source_height_m.ravel()[take],
        "receiver_height_m": roof.parameters.receiver_height_m.ravel()[take],
    }
