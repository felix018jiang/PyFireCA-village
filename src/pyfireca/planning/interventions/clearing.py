"""Vegetation removal by vector strips, with fractional cell-area accounting.

Partial cells use homogeneous remaining fuel, not a reconstructed subcell fire
barrier. Clearing creates no opaque wall and never blocks radiation by fiat.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from math import ceil, floor

import numpy as np
from shapely.geometry import LineString, box, mapping
from shapely.ops import unary_union

from pyfireca.planning.contracts import PointXY, require_id, require_number, require_point
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.transport_v3 import build_v3_radiative_transport_edges


class InterventionConflict(ValueError):
    """A proposed intervention conflicts with protected inputs or ignition."""


@dataclass(frozen=True, slots=True)
class ClearingStrip:
    start_m: PointXY
    end_m: PointXY
    width_m: float

    def __post_init__(self):
        require_point(self.start_m)
        require_point(self.end_m)
        require_number("width_m", self.width_m, positive=True)
        if self.start_m == self.end_m:
            raise ValueError("clearing strip must have positive length")


@dataclass(frozen=True, slots=True)
class ClearingPlan:
    plan_id: str
    strips: tuple[ClearingStrip, ...] = ()
    zone_id: str = "vegetation"

    def __post_init__(self):
        require_id("plan_id", self.plan_id)
        require_id("zone_id", self.zone_id)
        if not isinstance(self.strips, tuple) or any(
            not isinstance(strip, ClearingStrip) for strip in self.strips
        ):
            raise ValueError("strips must be a typed tuple")

    def to_mapping(self):
        return asdict(self)


# Areas and masses are already integrated over the occupied fraction of a cell.
# Scaling all these quantities together preserves the original burn curve and
# moisture relationships. Temperatures, heights and intensive fuel inputs stay.
EXTENSIVE = {
    "coverage_fraction",
    "covered_area_m2",
    "initial_dry_fuel_mass_kg",
    "live_dry_fuel_mass_kg",
    "dead_dry_fuel_mass_kg",
    "pyrolysable_dry_fuel_mass_kg",
    "live_water_mass_kg",
    "dead_water_mass_kg",
    "water_mass_kg",
    "peak_hrr_W",
    "effective_heated_dry_mass_kg",
    "receiver_area_m2",
    "active_surface_area_m2",
    "available_energy_J",
    "configured_curve_energy_J",
}


def prepare_clearing(request, plan, transport, *, allowed_mask=None, ignitions=()):
    """Apply removal in a new request; preserve every building and source cell.

    Ground-area intersection is measured exactly before equivalent-cell scaling.
    Only known burnable nonbuilding cells are eligible. Treatment overlaps are
    unioned; inert surfaces and excluded construction zones are not charged as
    cleared vegetation. The clipped footprint is exported explicitly.
    """
    if not isinstance(plan, ClearingPlan):
        raise TypeError("intervention must be ClearingPlan")
    fraction = np.zeros(request.grid.shape)
    summary = {
        **plan.to_mapping(),
        "kind": "hypothetical_vegetation_clearance",
        "evidence": "proposed_not_surveyed_or_built",
        "cleared_area_m2": 0.0,
        "affected_cells": 0,
        "fully_cleared_cells": 0,
        "removed_dry_fuel_kg": 0.0,
        "requested_area_m2": 0.0,
        "effective_footprint": mapping(unary_union([])),
        "partial_cell_model": "homogeneous_remaining_fuel_not_resolved_subcell_gap",
        "radiation_policy": "transparent_clearance_preserves_cross_strip_radiation",
    }
    if not plan.strips:
        return request, fraction, summary
    grid = request.grid
    eligible = request.burnable_mask & (request.building_index < 0)
    if allowed_mask is not None:
        allowed = np.asarray(allowed_mask)
        if allowed.dtype != np.bool_ or allowed.shape != grid.shape:
            raise ValueError("construction zone must be boolean and match grid")
        eligible &= allowed
    footprints = [
        LineString([s.start_m, s.end_m]).buffer(s.width_m / 2, cap_style="flat")
        for s in plan.strips
    ]
    footprint = unary_union(footprints)
    extent = box(
        grid.x_min_m,
        grid.y_max_m - grid.height * grid.cell_size_m,
        grid.x_min_m + grid.width * grid.cell_size_m,
        grid.y_max_m,
    )
    if not extent.covers(footprint):
        raise InterventionConflict("strip extends outside the declared domain envelope")
    summary["requested_area_m2"] = float(footprint.area)
    x0, y0, x1, y1 = footprint.bounds
    size = grid.cell_size_m
    r0, r1 = (
        max(0, floor((grid.y_max_m - y1) / size)),
        min(grid.height, ceil((grid.y_max_m - y0) / size)),
    )
    c0, c1 = (
        max(0, floor((x0 - grid.x_min_m) / size)),
        min(grid.width, ceil((x1 - grid.x_min_m) / size)),
    )
    effective = []
    for row in range(r0, r1):
        for col in range(c0, c1):
            if not eligible[row, col]:
                continue
            left, top = grid.x_min_m + col * size, grid.y_max_m - row * size
            part = footprint.intersection(box(left, top - size, left + size, top))
            value = float(part.area / grid.cell_area_m2)
            if value > 1e-12:
                fraction[row, col] = 1.0 if value >= 1 - 1e-12 else value
                effective.append(part)
    for event in (*request.scenario.ignitions, *ignitions):
        if fraction[event.row, event.col] > 0:
            raise InterventionConflict("clearing must not remove fuel from any ignition cell")
    remaining = 1 - fraction
    fields = {
        name: getattr(request.parameters, name).copy()
        for name in V3CellPhysicsArrays.__dataclass_fields__
    }
    for name in EXTENSIVE:
        fields[name] *= remaining
    physics = V3CellPhysicsArrays(**fields)
    burnable = request.burnable_mask & (remaining > 0)
    target = request.target_mask & (physics.receiver_area_m2 > 0)
    edges = build_v3_radiative_transport_edges(
        grid,
        burnable,
        target,
        physics.receiver_area_m2,
        physics.source_height_m,
        physics.receiver_height_m,
        np.ones(grid.shape),
        transport,
        ground_elevation_m=request.ground_elevation_m,
    )
    material, landcover = request.material_index.copy(), request.landcover_index.copy()
    cleared = fraction == 1
    material[cleared] = 0
    landcover[cleared] = 0
    changed = replace(
        request,
        parameters=physics,
        burnable_mask=burnable,
        source_mask=burnable,
        target_mask=target,
        piloted_ignition_mask=request.piloted_ignition_mask & burnable,
        material_index=material,
        landcover_index=landcover,
        exposure_edges=edges,
    )
    summary.update(
        cleared_area_m2=float(fraction.sum() * grid.cell_area_m2),
        affected_cells=int((fraction > 0).sum()),
        fully_cleared_cells=int(cleared.sum()),
        removed_dry_fuel_kg=float(np.sum(request.parameters.initial_dry_fuel_mass_kg * fraction)),
        effective_footprint=mapping(unary_union(effective)),
    )
    return changed, fraction, summary
