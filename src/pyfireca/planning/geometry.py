"""Vector treatment footprints and explicit overlap/cost semantics."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

from shapely.geometry import LineString, Polygon, box
from shapely.geometry.base import BaseGeometry
from shapely.ops import unary_union

from pyfireca.planning.contracts import Plan, PlanningGrid, Treatment, TreatmentProfile


def treatment_footprint(treatment: Treatment) -> Polygon:
    """Construct the actual flat-ended strip without snapping to raster cells."""
    footprint = LineString([treatment.start_m, treatment.end_m]).buffer(
        treatment.width_m / 2, cap_style="flat"
    )
    if (
        footprint.is_empty
        or not footprint.is_valid
        or not isfinite(footprint.area)
        or footprint.area <= 0
    ):
        raise ValueError("treatment geometry cannot be represented at this precision")
    return footprint


@dataclass(frozen=True, slots=True)
class TreatmentRegion:
    profile: TreatmentProfile
    footprint: BaseGeometry


@dataclass(frozen=True, slots=True)
class PreparedPlan:
    """Same-profile overlap is charged once; distinct-profile contact is rejected.

    Rejecting contact as well as overlap avoids an undeclared boundary tie rule
    for the P01 prototype. Junction composition is an explicit P02 extension.
    """

    plan: Plan
    regions: tuple[TreatmentRegion, ...]
    treated_area_m2: float
    cost_units: float


def prepare_plan(
    grid: PlanningGrid, plan: Plan, profiles: tuple[TreatmentProfile, ...]
) -> PreparedPlan:
    """Validate construction bounds and merge compatible physical footprints.

    P01 uses the whole rectangular local grid as the declared construction
    envelope. Out-of-envelope treatments fail, rather than silently clipping.
    No real construction-accessibility or protected-land assumptions are made.
    """
    if not isinstance(plan, Plan) or not isinstance(grid, PlanningGrid):
        raise TypeError("prepare_plan requires PlanningGrid and Plan")
    if not isinstance(profiles, tuple) or not all(
        isinstance(profile, TreatmentProfile) for profile in profiles
    ):
        raise TypeError("profiles must be a tuple of TreatmentProfile")
    lookup = {p.profile_id: p for p in profiles}
    if len(lookup) != len(profiles):
        raise ValueError("profile IDs must be unique")
    grouped: dict[str, list[Polygon]] = {}
    for treatment in plan.treatments:
        if treatment.profile_id not in lookup:
            raise ValueError(f"unknown treatment profile: {treatment.profile_id}")
        footprint = treatment_footprint(treatment)
        if not box(*grid.bounds).covers(footprint):
            raise ValueError("treatment extends outside declared construction envelope")
        grouped.setdefault(treatment.profile_id, []).append(footprint)
    regions = tuple(
        TreatmentRegion(lookup[key], unary_union(grouped[key])) for key in sorted(grouped)
    )
    for i, region in enumerate(regions):
        for other in regions[i + 1 :]:
            if region.footprint.intersects(other.footprint):
                raise ValueError(
                    "distinct treatment profiles overlap or touch; composition undefined"
                )
    area = sum(region.footprint.area for region in regions)
    cost = sum(region.footprint.area * region.profile.cost_units_per_m2 for region in regions)
    if not isfinite(area) or not isfinite(cost):
        raise ValueError("treatment area or cost overflows")
    return PreparedPlan(plan, regions, area, cost)
