"""Static geometric intervention adapter reusing the unchanged arrival solver.

P01 scope: local north-up grid, immediate neighbours, source-cell background
ROS, point targets mapped to cells, no spotting/thermal crossing or dynamic
wind. M0 includes its boundary and blocks tangencies. M1 integrates positive
covered edge length, including travel on its boundary. Rates are evaluated at
the source cell in the edge direction; heterogeneous subcell fuel integration
and independent convergence validation belong to P02.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import inf, isfinite

import numpy as np
from numpy.typing import NDArray
from shapely import intersects_xy
from shapely.geometry import LineString, Point

from pyfireca.arrival import DirectionalSpreadRateProvider, StaticArrivalTimeSolver
from pyfireca.neighborhood import MooreNeighborhood, Neighborhood, Offset
from pyfireca.planning.contracts import Plan, PlanningGrid, Scenario, TreatmentProfile
from pyfireca.planning.geometry import PreparedPlan, prepare_plan

CAPABILITIES = {
    "schema": "planning-local-static-2",
    "coordinate_frame": "local_metre",
    "supports_subcell_treatment_geometry": True,
    "supports_time_varying_wind": False,
    "supports_spotting": False,
    "supports_thermal_crossing": False,
    "supports_rotated_grid": False,
    "background_edge_semantics": "source_cell",
    "target_semantics": "point_mapped_to_containing_cell",
    "nonburnable_corner_policy": "conservative_supercover",
    "treatment_representations": ["vector", "centre_mask"],
}


def _rate(provider: DirectionalSpreadRateProvider, row: int, col: int, offset: Offset) -> float:
    value = provider.spread_rate_m_s(row, col, offset)
    if isinstance(value, bool) or not isfinite(value) or value < 0:
        raise ValueError("ROS must be finite and non-negative")
    return value


@dataclass(frozen=True, slots=True)
class GeometrySpreadRate:
    """Convert explicit edge travel time into the existing equivalent ROS API."""

    grid: PlanningGrid
    prepared: PreparedPlan
    background: DirectionalSpreadRateProvider
    domain: NDArray[np.bool_]

    def spread_rate_m_s(self, row: int, col: int, offset: Offset) -> float:
        dr, dc = offset
        # A diagonal touches the two flank cells. P01 conservatively forbids
        # corner cutting through a nonburnable/excluded flank in the input mask.
        if dr and dc and (not self.domain[row + dr, col] or not self.domain[row, col + dc]):
            return 0.0
        if not self.prepared.regions:
            return _rate(self.background, row, col, offset)
        edge = LineString([self.grid.centre(row, col), self.grid.centre(row + dr, col + dc)])
        covered: list[tuple[float, TreatmentProfile]] = []
        for region in self.prepared.regions:
            if not edge.intersects(region.footprint):
                continue
            if region.profile.mode == "M0":
                return 0.0
            length = edge.intersection(region.footprint).length
            if length > 0:
                covered.append((length, region.profile))
        if not covered:
            return _rate(self.background, row, col, offset)
        total = sum(length for length, _ in covered)
        # No positive-length overlap is permitted. Allow only floating-point
        # summation tolerance, never erode narrow strips by a user tolerance.
        if total > edge.length + 1e-12 * edge.length:
            raise ValueError("covered lengths exceed edge length")
        outside = max(0.0, edge.length - total)
        travel = 0.0
        if outside > 0:
            rate = _rate(self.background, row, col, offset)
            if rate == 0:
                return 0.0
            travel = outside / rate
        for length, profile in covered:
            rate = _rate(profile.rate_provider, row, col, offset)
            if rate == 0:
                return 0.0
            travel += length / rate
        if not isfinite(travel) or travel <= 0:
            raise ValueError("edge travel time overflows or is not positive")
        return edge.length / travel


@dataclass(frozen=True, slots=True)
class CentreMaskSpreadRate:
    """Explicit numerical comparator, not a replacement for vector treatments.

    M1 selects a source-centre profile for the entire outgoing edge. M0 masks
    centres and prohibits diagonal corner cutting through the resulting mask.
    Physical construction cost remains the vector-union cost in either model.
    """

    prepared: PreparedPlan
    labels: NDArray[np.int64]
    background: DirectionalSpreadRateProvider
    domain: NDArray[np.bool_]

    def spread_rate_m_s(self, row: int, col: int, offset: Offset) -> float:
        dr, dc = offset
        if dr and dc and (not self.domain[row + dr, col] or not self.domain[row, col + dc]):
            return 0.0
        label = self.labels[row, col]
        provider = self.background
        if label >= 0:
            profile = self.prepared.regions[label].profile
            if profile.mode == "M0":
                return 0.0
            provider = profile.rate_provider
        return _rate(provider, row, col, offset)


def centre_labels(grid: PlanningGrid, prepared: PreparedPlan) -> NDArray[np.int64]:
    """Rasterize closed treatment footprints at centres for an explicit control."""
    yy, xx = np.indices(grid.shape, dtype=float)
    xx = grid.x_min_m + (xx + 0.5) * grid.cell_size_m
    yy = grid.y_max_m - (yy + 0.5) * grid.cell_size_m
    labels = np.full(grid.shape, -1, dtype=np.int64)
    for index, region in enumerate(prepared.regions):
        labels[intersects_xy(region.footprint, xx, yy)] = index
    labels.setflags(write=False)
    return labels


@dataclass(frozen=True, slots=True)
class Evaluation:
    """Finite-horizon exposure and graph reachability are separate outputs."""

    plan_id: str
    scenario_id: str
    arrival_times_s: NDArray[np.float64]
    target_arrivals_s: tuple[float, ...]
    exposed_weight: float
    reached_cell_count_at_horizon: int
    unreachable_burnable_cell_count: int
    boundary_contact_at_horizon: bool
    treated_area_m2: float
    cost_units: float
    representation: str


def evaluate(
    *,
    grid: PlanningGrid,
    domain_mask: NDArray[np.bool_],
    background: DirectionalSpreadRateProvider,
    plan: Plan,
    scenario: Scenario,
    profiles: tuple[TreatmentProfile, ...] = (),
    neighborhood: Neighborhood | None = None,
    representation: str = "vector",
) -> Evaluation:
    """Evaluate one layout without changing input arrays or target membership.

    Ignition/target points AND their mapped centres must avoid all treatments;
    this deliberately rejects ambiguous coarse-grid placement in P01. Targets
    cannot disappear from the denominator through construction. False domain
    cells are explicitly excluded, not automatically interpreted as success.
    """
    if not isinstance(scenario, Scenario):
        raise TypeError("scenario must be Scenario")
    if representation not in ("vector", "centre_mask"):
        raise ValueError("representation must be vector or centre_mask")
    source_domain = np.asarray(domain_mask)
    if source_domain.shape != grid.shape or source_domain.dtype != np.bool_:
        raise ValueError("domain_mask must be boolean and match grid shape")
    if not callable(getattr(background, "spread_rate_m_s", None)):
        raise TypeError("background must supply directional ROS")
    prepared = prepare_plan(grid, plan, profiles)
    domain = source_domain.copy()
    for item in (*scenario.ignitions, *scenario.targets):
        row, col = grid.cell_at(item.point_m)
        if not source_domain[row, col]:
            raise ValueError("ignition/target must be in the declared burnable domain")
        for region in prepared.regions:
            if region.footprint.covers(Point(item.point_m)) or region.footprint.covers(
                Point(grid.centre(row, col))
            ):
                raise ValueError("ignition/target or its mapped centre conflicts with treatment")
    labels = centre_labels(grid, prepared)
    for index, region in enumerate(prepared.regions):
        if region.profile.mode == "M0":
            domain &= labels != index
    ignition = np.full(grid.shape, inf)
    for item in scenario.ignitions:
        cell = grid.cell_at(item.point_m)
        ignition[cell] = min(ignition[cell], item.time_s)
    # Keep the original excluded-cell corner policy independent of treatment
    # centre masking; subcell M0 intersections are checked as vector geometry.
    provider = (
        GeometrySpreadRate(grid, prepared, background, source_domain.copy())
        if representation == "vector"
        else CentreMaskSpreadRate(prepared, labels, background, domain.copy())
    )
    solver = StaticArrivalTimeSolver(
        MooreNeighborhood() if neighborhood is None else neighborhood,
        grid.cell_size_m,
        provider,
    )
    arrival = solver.solve(domain, ignition)
    reached = domain & (arrival <= scenario.horizon_s)
    target_arrivals = tuple(float(arrival[grid.cell_at(t.point_m)]) for t in scenario.targets)
    loss = sum(
        t.weight
        for t, time in zip(scenario.targets, target_arrivals, strict=True)
        if time <= scenario.horizon_s
    )
    boundary = bool(
        reached[0].any() or reached[-1].any() or reached[:, 0].any() or reached[:, -1].any()
    )
    arrival.setflags(write=False)
    return Evaluation(
        plan.plan_id,
        scenario.scenario_id,
        arrival,
        target_arrivals,
        loss,
        int(reached.sum()),
        int(np.count_nonzero(domain & ~np.isfinite(arrival))),
        boundary,
        prepared.treated_area_m2,
        prepared.cost_units,
        representation,
    )


@dataclass(frozen=True, slots=True)
class PairedEvaluation:
    baseline: Evaluation
    intervention: Evaluation
    exposure_reduction: float
    horizon_capped_target_delay_s: tuple[float, ...]


def evaluate_pair(**kwargs) -> PairedEvaluation:
    """Rerun both layouts on identical fixed inputs; cap delays at the horizon.

    Full graph arrivals (including infinity) remain available in each result.
    This deterministic adapter has no RNG, cache or inferred annual probability.
    """
    if "plan" not in kwargs or "scenario" not in kwargs:
        raise TypeError("evaluate_pair requires plan and scenario")
    baseline = evaluate(**(kwargs | {"plan": Plan("no-treatment")}))
    intervention = evaluate(**kwargs)
    horizon = kwargs["scenario"].horizon_s
    delays = tuple(
        min(after, horizon) - min(before, horizon)
        for before, after in zip(
            baseline.target_arrivals_s, intervention.target_arrivals_s, strict=True
        )
    )
    return PairedEvaluation(
        baseline, intervention, baseline.exposed_weight - intervention.exposed_weight, delays
    )
