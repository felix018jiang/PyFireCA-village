"""Refresh registered building geometry and audit mesh independence, without fire search."""

from __future__ import annotations

from dataclasses import asdict, replace

import numpy as np
from shapely.geometry import box

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.interventions.buildings import _raster
from pyfireca.planning.interventions.clearing import EXTENSIVE
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges


def equivalent_assembly(request, mask):
    """Require a uniform evidenced reference, rather than fitting new parameters."""
    area = request.parameters.covered_area_m2[mask]
    if not len(area) or np.any(area <= 0):
        raise ValueError("positive reference coverage is required")
    reference = {}
    for name in V3CellPhysicsArrays.__dataclass_fields__:
        if name in {"coverage_fraction", "covered_area_m2"}:
            continue
        value = getattr(request.parameters, name)[mask]
        if name in EXTENSIVE:
            value = value / area
        if not np.allclose(value, value[0], rtol=1e-9, atol=1e-9):
            raise ValueError(f"nonuniform reference assembly: {name}")
        reference[name] = float(value[0])
    return reference


def refresh_building_geometry(bundle, inventory, transport, *, ground_material, ground_landcover):
    """Rebuild changed roof cells using registered RGB ground and parent assemblies.

    The supplied ground is the pre-roof RGB classification, not an assumed
    post-demolition surface. All ordinary scene geometry remains active.
    """
    request = bundle.request
    before = request_fingerprint(request)
    if request.firebrands is not None:
        raise ValueError("geometry refresh has not been verified with firebrands")
    verified = build_v3_radiative_transport_edges(
        request.grid,
        request.source_mask,
        request.target_mask,
        request.parameters.receiver_area_m2,
        request.parameters.source_height_m,
        request.parameters.receiver_height_m,
        np.ones(request.grid.shape),
        transport,
        ground_elevation_m=request.ground_elevation_m,
    )
    if any(
        not np.array_equal(getattr(verified, name), getattr(request.exposure_edges, name))
        for name in ("source", "target", "weight")
    ):
        raise ValueError("unsupported parent radiation graph; transparent-point match required")
    del verified
    shape = request.grid.shape
    for a in (ground_material, ground_landcover):
        if a.shape != shape or not np.issubdtype(a.dtype, np.integer):
            raise ValueError("registered ground indices must match the physical grid")
    old_roofs = request.building_index >= 0
    ground = request.domain_mask & ~old_roofs
    if not np.array_equal(
        ground_material[ground], request.material_index[ground]
    ) or not np.array_equal(ground_landcover[ground], request.landcover_index[ground]):
        raise ValueError("RGB ground does not reproduce the parent nonbuilding context")
    _structures, _rows, coverage, owner = _raster(inventory, request.grid)
    extent = box(
        request.grid.x_min_m,
        request.grid.y_max_m - request.grid.height * request.grid.cell_size_m,
        request.grid.x_min_m + request.grid.width * request.grid.cell_size_m,
        request.grid.y_max_m,
    )
    if any(not extent.covers(b.geometry) for b in inventory.buildings):
        raise ValueError("updated outline extends outside the grid envelope")
    roofs = owner >= 0
    if np.any(roofs & ~request.domain_mask):
        raise ValueError("updated outlines extend outside the physical domain")
    if any(not inventory.by_id[i].geometry.is_valid for i in inventory.by_id):
        raise ValueError("invalid updated geometry")
    if (
        len(set(map(int, request.material_index[old_roofs]))) != 1
        or len(set(map(int, request.landcover_index[old_roofs]))) != 1
    ):
        raise ValueError("refresh requires a single equivalent building assembly")
    building_material = int(request.material_index[old_roofs][0])
    building_landcover = int(request.landcover_index[old_roofs][0])
    assembly = equivalent_assembly(request, old_roofs)
    changed = (owner != request.building_index) | (
        roofs & ~np.isclose(coverage, request.parameters.coverage_fraction, rtol=1e-7, atol=1e-9)
    )
    changed &= request.domain_mask
    fields = {
        name: getattr(request.parameters, name).copy()
        for name in V3CellPhysicsArrays.__dataclass_fields__
    }

    def assign(mask, area, reference):
        fields["covered_area_m2"][mask] = area
        fields["coverage_fraction"][mask] = area / request.grid.cell_area_m2
        for name, value in reference.items():
            fields[name][mask] = value * area if name in EXTENSIVE else value

    roof_changes = changed & roofs
    assign(roof_changes, coverage[roof_changes].astype(float) * request.grid.cell_area_m2, assembly)
    material, landcover = request.material_index.copy(), request.landcover_index.copy()
    material[roof_changes], landcover[roof_changes] = building_material, building_landcover
    exposed = changed & ~roofs
    for code in np.unique(ground_material[exposed]):
        mask = exposed & (ground_material == code)
        reference = ground & (request.material_index == code)
        if not reference.any():
            raise ValueError("exposed ground has no matching parent reference assembly")
        assign(
            mask,
            np.full(int(mask.sum()), request.grid.cell_area_m2),
            equivalent_assembly(request, reference),
        )
        material[mask], landcover[mask] = code, ground_landcover[mask]
    physics = V3CellPhysicsArrays(**fields)
    burnable = request.burnable_mask.copy()
    burnable[changed] = physics.pyrolysable_dry_fuel_mass_kg[changed] > 0
    target = request.target_mask.copy()
    target[changed] = physics.receiver_area_m2[changed] > 0
    pilot = request.piloted_ignition_mask.copy()
    pilot[changed] = burnable[changed]
    evidence = (
        None if request.material_evidence_index is None else request.material_evidence_index.copy()
    )
    if evidence is not None:
        evidence[roof_changes], evidence[exposed] = 2, 1
    graph = build_v3_radiative_transport_edges(
        request.grid,
        burnable,
        target,
        physics.receiver_area_m2,
        physics.source_height_m,
        physics.receiver_height_m,
        np.ones(shape),
        transport,
        ground_elevation_m=request.ground_elevation_m,
    )
    changed_request = replace(
        request,
        parameters=physics,
        building_index=owner,
        material_index=material,
        landcover_index=landcover,
        burnable_mask=burnable,
        source_mask=burnable,
        target_mask=target,
        piloted_ignition_mask=pilot,
        exposure_edges=graph,
        material_evidence_index=evidence,
    )
    if request_fingerprint(request) != before:
        raise AssertionError("geometry refresh changed its parent")
    return changed_request, {
        "schema_version": "building-geometry-refresh-1",
        "parent_case_sha256": before,
        "refreshed_request_sha256": request_fingerprint(changed_request),
        "changed_cells": int(changed.sum()),
        "changed_roof_cells": int(roof_changes.sum()),
        "exposed_ground_cells": int(exposed.sum()),
        "old_building_area_m2": float(request.parameters.covered_area_m2[old_roofs].sum()),
        "new_building_area_m2": float(physics.covered_area_m2[roofs].sum()),
        "removed_buildings": [],
        "all_declared_buildings_retained": True,
        "ground_policy": "registered_pre_roof_RGB_hypothesis_not_measured_or_cleared_ground",
        "ignition_policy": "inherited_technical_event_not_a_frozen_study_source",
        "thermal_simulations_performed": 0,
    }


def audit_target_mesh(inventory, grid, target_id):
    """Measure shared states and cell-face contacts with every local neighbour."""
    geometry = inventory.by_id[target_id].geometry
    xmin, ymin, xmax, ymax = geometry.bounds
    if not (
        grid.x_min_m <= xmin
        and grid.x_min_m + grid.width * grid.cell_size_m >= xmax
        and grid.y_max_m >= ymax
        and grid.y_max_m - grid.height * grid.cell_size_m <= ymin
    ):
        raise ValueError("mesh must contain the entire target outline")
    _structures, rows, coverage, owner = _raster(inventory, grid)
    count = np.zeros(grid.shape, np.int32)
    target_area = np.zeros(grid.shape)
    neighbours = set()
    for identifier, row, col, area in rows:
        count[row, col] += 1
        neighbours.add(identifier)
        if identifier == target_id:
            target_area[row, col] = area
    target = target_area > 0
    number = inventory.by_id[target_id].object_id
    contacts = {}
    reverse = {b.object_id: b.building_id for b in inventory.buildings}
    for a, b in ((owner[:-1], owner[1:]), (owner[:, :-1], owner[:, 1:])):
        ids = np.concatenate(
            (
                b[(a == number) & (b >= 0) & (b != number)],
                a[(b == number) & (a >= 0) & (a != number)],
            )
        )
        for value in np.unique(ids):
            identifier = reverse[int(value)]
            contacts[identifier] = contacts.get(identifier, 0) + int((ids == value).sum())
    face_pairs = [
        {
            "other_id": i,
            "cell_face_count": n,
            "vector_gap_m": float(geometry.distance(inventory.by_id[i].geometry)),
        }
        for i, n in sorted(contacts.items())
    ]
    overlap = [
        {
            "other_id": i,
            "overlap_m2": float(geometry.intersection(inventory.by_id[i].geometry).area),
        }
        for i in sorted(neighbours - {target_id})
        if geometry.intersection(inventory.by_id[i].geometry).area > 1e-6
    ]
    report = {
        "target_id": target_id,
        "grid": asdict(grid),
        "target_vector_area_m2": float(geometry.area),
        "target_observed_area_m2": float(target_area.sum()),
        "target_area_absolute_error_m2": abs(float(target_area.sum()) - float(geometry.area)),
        "shared_temperature_cells": int(((count > 1) & target).sum()),
        "target_area_without_own_fuel_id_m2": float(target_area[owner != number].sum()),
        "different_building_cell_face_count": sum(p["cell_face_count"] for p in face_pairs),
        "positive_gap_cell_face_count": sum(
            p["cell_face_count"] for p in face_pairs if p["vector_gap_m"] > 1e-8
        ),
        "cell_face_pairs": face_pairs,
        "true_outline_overlaps": overlap,
        "local_represented_object_count": len(neighbours),
        "local_building_union_area_m2": float(coverage.astype(float).sum() * grid.cell_area_m2),
        "scope": "geometry_only_all_intersecting_buildings_no_thermal_or_safety_claim",
    }
    report["independent_target_geometry"] = (
        report["shared_temperature_cells"] == 0
        and report["positive_gap_cell_face_count"] == 0
        and report["target_area_absolute_error_m2"] < 1e-5
        and not overlap
    )
    arrays = {
        "building_index": owner,
        "coverage_fraction": coverage,
        "object_count": count,
        "target_intersection_area_m2": target_area,
    }
    return report, arrays
