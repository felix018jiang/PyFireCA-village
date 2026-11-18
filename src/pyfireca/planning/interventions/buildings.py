"""Whole-building demolition with vector re-rasterization and thermal closure.

P1 supports an explicit cleared, inert post-demolition surface. Shared cells
remain equivalent homogeneous fuel; they are not resolved independent houses.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace

import numpy as np
from shapely.geometry import mapping
from shapely.ops import unary_union

from pyfireca.planning.contracts import require_id
from pyfireca.planning.interventions.clearing import EXTENSIVE, InterventionConflict
from pyfireca.planning.observations import build_object_observations
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges
from pyfireca.village.vegetation_prepare import building_cell_intersections


@dataclass(frozen=True, slots=True)
class BuildingDemolitionPlan:
    plan_id: str
    demolished_ids: tuple[str, ...] = ()
    ground_policy: str = "cleared_inert"
    ground_evidence: str = ""
    overlap_policy: str = "reject"

    def __post_init__(self):
        require_id("plan_id", self.plan_id)
        if (
            not isinstance(self.demolished_ids, tuple)
            or any(not isinstance(i, str) or not i.strip() for i in self.demolished_ids)
            or len(set(self.demolished_ids)) != len(self.demolished_ids)
        ):
            raise ValueError("demolished_ids must be unique string IDs")
        if self.ground_policy != "cleared_inert":
            raise ValueError("P1 supports only explicitly cleared inert ground")
        if self.overlap_policy not in {"reject", "retain_shared_roof"}:
            raise ValueError("unsupported demolition overlap policy")
        if self.demolished_ids:
            require_id("ground_evidence", self.ground_evidence)

    def to_mapping(self):
        return asdict(self)


def _raster(inventory, grid, removed=()):
    structures = tuple(b.record() for b in inventory.buildings if b.building_id not in removed)
    rows, coverage, _ = building_cell_intersections(structures, grid)
    owner = np.full(grid.shape, -1, dtype=np.int32)
    maximum = np.zeros(grid.shape)
    by_id = inventory.by_id
    for identifier, row, col, area in rows:
        if area > maximum[row, col]:
            maximum[row, col] = area
            owner[row, col] = by_id[identifier].object_id
    return structures, rows, coverage, owner


def _check_shared_assembly(request, inventory, rows, changed):
    """Do not assign a removed owner's different assembly to its neighbour."""
    references = {}
    for identifier, row, col, _area in rows:
        if not changed[row, col]:
            continue
        number = inventory.by_id[identifier].object_id
        if number not in references:
            mask = request.building_index == number
            area = request.parameters.covered_area_m2[mask]
            if not len(area) or np.any(area <= 0):
                raise InterventionConflict("retained shared-cell assembly has no reference")
            reference = {}
            for name in V3CellPhysicsArrays.__dataclass_fields__:
                if name in {"coverage_fraction", "covered_area_m2"}:
                    continue
                values = getattr(request.parameters, name)[mask]
                if name in EXTENSIVE:
                    values = values / area
                if not np.allclose(values, values[0], rtol=1e-8, atol=1e-8):
                    raise InterventionConflict("nonuniform retained shared-cell assembly")
                reference[name] = values[0]
            references[number] = reference
        area = request.parameters.covered_area_m2[row, col]
        for name, reference in references[number].items():
            value = getattr(request.parameters, name)[row, col]
            if name in EXTENSIVE:
                value /= area
            if not np.isclose(value, reference, rtol=1e-8, atol=1e-8):
                raise InterventionConflict("different assemblies in shared demolition cell")


def prepare_building_demolition(request, plan, inventory, transport):
    """Return a fresh physical request, rebound observer and intervention ledger."""
    if not isinstance(plan, BuildingDemolitionPlan):
        raise TypeError("intervention must be BuildingDemolitionPlan")
    if plan.overlap_policy != "reject":
        raise ValueError("shared-roof retention requires the independent roof engine")
    if request.firebrands is not None:
        raise ValueError("P1 demolition is not yet verified with firebrands")
    removed = set(plan.demolished_ids)
    if not removed <= set(inventory.candidate_ids):
        raise InterventionConflict("demolition requires declared ordinary candidate IDs")
    structures, original_rows, original_coverage, _ = _raster(inventory, request.grid)
    occupied = original_coverage > 0
    if not np.array_equal(occupied, request.building_index >= 0):
        raise ValueError("inventory geometry does not match the parent building raster")
    binding_valid = ~occupied
    by_id = inventory.by_id
    for identifier, row, col, _area in original_rows:
        if request.building_index[row, col] == by_id[identifier].object_id:
            binding_valid[row, col] = True
    if not binding_valid.all():
        raise ValueError("numeric building identities do not match parent ownership")
    if not np.allclose(
        request.parameters.coverage_fraction[occupied],
        original_coverage[occupied],
        rtol=1e-6,
        atol=1e-8,
    ):
        raise ValueError("inventory area does not match parent physical coverage")
    for b in inventory.buildings:
        if b.building_id not in removed:
            continue
        for other in inventory.buildings:
            if (
                other.building_id != b.building_id
                and b.geometry.intersection(other.geometry).area > 1e-6
            ):
                raise InterventionConflict("overlapping annotations need semantic review")
    ledger = {
        **plan.to_mapping(),
        "kind": "whole_building_demolition",
        "inventory_sha256": inventory.sha256,
        "candidate_evidence": inventory.candidate_evidence,
        "demolished_count": len(removed),
        "demolished_footprint_area_m2": sum(
            float(inventory.by_id[i].geometry.area) for i in removed
        ),
        "affected_household_count": None,
        "implementation_feasibility_verified": False,
        "partial_cell_model": "homogeneous_retained_roof_with_inert_uncovered_fraction",
        "radiation_policy": "rebuild_transparent_point_graph_no_artificial_wall",
        "demolished_geometry": mapping(
            unary_union([inventory.by_id[i].geometry for i in sorted(removed)])
        ),
    }
    if not removed:
        observer = build_object_observations(request, structures, inventory.labels)
        return request, observer, {**ledger, "affected_cells": 0, "removed_dry_fuel_kg": 0.0}
    structures, rows, coverage, owner = _raster(inventory, request.grid, removed)
    fraction = np.ones(request.grid.shape)
    fraction[occupied] = np.minimum(1.0, coverage[occupied] / original_coverage[occupied])
    changed = occupied & (fraction < 1.0)
    # Keep the parent's ownership outside the actual action, including tie choices.
    owner[~changed] = request.building_index[~changed]
    _check_shared_assembly(request, inventory, rows, changed)
    empty = occupied & (coverage == 0)
    fields = {
        name: getattr(request.parameters, name).copy()
        for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    for name, value in fields.items():
        if name in EXTENSIVE:
            value[changed] *= fraction[changed]
        value[empty] = 0.0
    physics = V3CellPhysicsArrays(**fields)
    burnable = request.burnable_mask & ~empty
    target = request.target_mask & ~empty
    edges = build_v3_radiative_transport_edges(
        request.grid,
        burnable,
        target,
        physics.receiver_area_m2,
        physics.source_height_m,
        physics.receiver_height_m,
        np.ones(request.grid.shape),
        transport,
        ground_elevation_m=request.ground_elevation_m,
    )
    material, landcover = request.material_index.copy(), request.landcover_index.copy()
    material[empty], landcover[empty] = 0, 0
    evidence = (
        None if request.material_evidence_index is None else request.material_evidence_index.copy()
    )
    if evidence is not None:
        evidence[changed] = 3  # Explicit intervention hypothesis, not a new observation.
    result = replace(
        request,
        parameters=physics,
        building_index=owner,
        burnable_mask=burnable,
        source_mask=burnable,
        target_mask=target,
        piloted_ignition_mask=request.piloted_ignition_mask & burnable,
        material_index=material,
        landcover_index=landcover,
        exposure_edges=edges,
        material_evidence_index=evidence,
    )
    labels = {k: v for k, v in inventory.labels.items() if v not in removed}
    observer = build_object_observations(result, structures, labels)
    return (
        result,
        observer,
        {
            **ledger,
            "affected_cells": int(changed.sum()),
            "removed_dry_fuel_kg": float(
                request.parameters.initial_dry_fuel_mass_kg.sum()
                - result.parameters.initial_dry_fuel_mass_kg.sum()
            ),
        },
    )
