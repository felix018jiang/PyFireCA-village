"""P02B bounded continuous-edge research graph, separate from raster arrival.

Nodes are physical ignition/target points, sampled treatment boundaries and
raster interfaces. Edges integrate *every* crossed raster cell and treatment.
Finite boundary sampling yields a feasible-path upper bound in the declared
piecewise-constant isotropic model, not a global continuous-optimum guarantee.
"""

from __future__ import annotations

from dataclasses import dataclass
from heapq import heappop, heappush
from itertools import pairwise
from math import ceil, floor, hypot, inf, isfinite

import numpy as np
from shapely.geometry import LineString, Point, box
from shapely.ops import unary_union

from pyfireca.arrival import ConstantDirectionalSpreadRate
from pyfireca.planning.contracts import (
    Plan,
    PlanningGrid,
    PointXY,
    Scenario,
    TreatmentProfile,
    require_number,
    require_point,
)
from pyfireca.planning.geometry import PreparedPlan, prepare_plan

CAPABILITIES = {
    "schema": "planning-visibility-isotropic-2",
    "coordinate_frame": "local_metre",
    "target_semantics": "physical_point",
    "background_semantics": "piecewise_constant_raster_cell",
    "treatment_semantics": "explicit_vector_edge_intervals",
    "excluded_cell_semantics": "closed_cell_supercover",
    "clearance_semantics": "candidate_node_offset_not_global_minimum_path_distance",
    "supports_anisotropic_ros": False,
    "supports_time_varying_wind": False,
    "supports_spotting": False,
    "supports_thermal_crossing": False,
    "global_continuum_optimality_guaranteed": False,
    "finite_m1_end_bypass_nodes": True,
}


class VisibilityResourceLimit(ValueError):
    """Numerical resource limit, not a physically infeasible construction."""


class VisibilityPhysicalConflict(ValueError):
    """A fixed physical ignition/target conflicts with the permitted domain."""


@dataclass(frozen=True, slots=True)
class VisibilityConfig:
    """Node discretization and resource limits, not a construction safety rule.

    ``clearance_m`` offsets obstacle candidate nodes; every edge still tests
    the original closed obstacle. It is not a guaranteed path-wide clearance.
    """

    boundary_spacing_m: float = 8.0
    clearance_m: float = 0.01
    max_nodes: int = 256

    def __post_init__(self):
        require_number("boundary_spacing_m", self.boundary_spacing_m, positive=True)
        require_number("clearance_m", self.clearance_m, positive=True)
        if (
            isinstance(self.max_nodes, bool)
            or not isinstance(self.max_nodes, int)
            or self.max_nodes < 2
        ):
            raise ValueError("max_nodes must be an integer >=2")


def _polygons(geometry):
    if geometry.geom_type == "Polygon":
        yield geometry
    else:
        for component in geometry.geoms:
            yield from _polygons(component)


def _line_parts(geometry):
    if geometry.geom_type in ("LineString", "LinearRing"):
        yield geometry
    elif hasattr(geometry, "geoms"):
        for component in geometry.geoms:
            yield from _line_parts(component)


class IntegratedEdges:
    """Long-edge travel integrator; all input arrays are copied and read-only."""

    def __init__(self, grid, domain_mask, background_rates_m_s, prepared: PreparedPlan):
        self.grid = grid
        domain = np.asarray(domain_mask)
        rates = np.asarray(background_rates_m_s)
        if domain.shape != grid.shape or domain.dtype != np.bool_:
            raise ValueError("domain_mask must be boolean and match grid")
        if (
            rates.shape != grid.shape
            or rates.dtype.kind not in "fiu"
            or not np.isfinite(rates).all()
            or (rates < 0).any()
        ):
            raise ValueError("background rates must be a finite non-negative numeric raster")
        self.domain = domain.copy()
        self.rates = np.array(rates, dtype=float, copy=True)
        self.domain.setflags(write=False)
        self.rates.setflags(write=False)
        self.prepared = prepared
        self.full_domain = bool(domain.all())
        self.uniform_rate = (
            float(self.rates.flat[0]) if np.all(self.rates == self.rates.flat[0]) else None
        )
        self.treated_rates = []
        for region in prepared.regions:
            if region.profile.mode == "M0":
                self.treated_rates.append(None)
            else:
                provider = region.profile.rate_provider
                if not isinstance(provider, ConstantDirectionalSpreadRate):
                    raise ValueError("visibility M1 requires an explicit isotropic constant ROS")
                require_number("treated rate", provider.rate_m_s)
                self.treated_rates.append(float(provider.rate_m_s))

    def point_allowed(self, point: PointXY) -> bool:
        """Conservative closed-cell contact with excluded cells, including ties."""
        require_point(point)
        try:
            self.grid.cell_at(point)
        except ValueError:
            return False
        if self.full_domain:
            return True
        x = (point[0] - self.grid.x_min_m) / self.grid.cell_size_m
        y = (self.grid.y_max_m - point[1]) / self.grid.cell_size_m
        cols, rows = [floor(x)], [floor(y)]
        # A numerical tie only expands excluded-cell checks, never erodes M0.
        if abs(x - round(x)) <= 1e-12:
            cols = [round(x) - 1, round(x)]
        if abs(y - round(y)) <= 1e-12:
            rows = [round(y) - 1, round(y)]
        return all(
            self.domain[r, c]
            for r in rows
            for c in cols
            if 0 <= r < self.grid.shape[0] and 0 <= c < self.grid.shape[1]
        )

    def travel_time_s(self, source: PointXY, target: PointXY) -> float:
        require_point(source)
        require_point(target)
        if not self.point_allowed(source) or not self.point_allowed(target):
            return inf
        dx, dy = target[0] - source[0], target[1] - source[1]
        length = hypot(dx, dy)
        if length == 0:
            return 0.0
        edge = LineString([source, target])
        cuts = {0.0, 1.0}
        intervals = []
        for index, region in enumerate(self.prepared.regions):
            if not edge.intersects(region.footprint):
                continue
            if region.profile.mode == "M0":
                return inf
            intersection = edge.intersection(region.footprint)
            for part in _line_parts(intersection):
                if part.length <= 0:
                    continue
                fractions = [
                    ((x - source[0]) * dx + (y - source[1]) * dy) / length**2
                    for x, y in part.coords
                ]
                low, high = max(0.0, min(fractions)), min(1.0, max(fractions))
                if high > low:
                    intervals.append((low, high, self.treated_rates[index]))
                    cuts.update((low, high))
        if not self.full_domain or self.uniform_rate is None:
            for a, b, origin, step in (
                (source[0], target[0], self.grid.x_min_m, self.grid.cell_size_m),
                (source[1], target[1], self.grid.y_max_m, self.grid.cell_size_m),
            ):
                if a == b:
                    continue
                for k in range(
                    floor((min(a, b) - origin) / step) + 1, ceil((max(a, b) - origin) / step)
                ):
                    t = (origin + k * step - a) / (b - a)
                    if 0 < t < 1:
                        cuts.add(t)
        ordered = sorted(cuts)
        if not self.full_domain:
            for t in ordered:
                if not self.point_allowed((source[0] + t * dx, source[1] + t * dy)):
                    return inf
        travel = 0.0
        for low, high in pairwise(ordered):
            middle = (low + high) / 2
            point = (source[0] + middle * dx, source[1] + middle * dy)
            if not self.point_allowed(point):
                return inf
            rate = (
                self.uniform_rate
                if self.uniform_rate is not None
                else float(self.rates[self.grid.cell_at(point)])
            )
            for start, end, treated_rate in intervals:
                if start <= middle <= end:
                    rate = treated_rate
                    break
            if rate == 0:
                return inf
            travel += length * (high - low) / rate
        if not isfinite(travel) or travel <= 0:
            raise ValueError("edge travel time cannot be represented")
        return travel


@dataclass(frozen=True, slots=True)
class VisibilityEvaluation:
    plan_id: str
    scenario_id: str
    node_points_m: tuple[PointXY, ...]
    node_arrivals_s: tuple[float, ...]
    target_arrivals_s: tuple[float, ...]
    exposed_weight: float
    treated_area_m2: float
    cost_units: float
    node_count: int
    tested_edge_count: int
    finite_edge_count: int
    config: VisibilityConfig
    target_paths_m: tuple[tuple[PointXY, ...], ...]


def evaluate_visibility(
    *,
    grid: PlanningGrid,
    domain_mask,
    background_rates_m_s,
    plan: Plan,
    scenario: Scenario,
    profiles: tuple[TreatmentProfile, ...] = (),
    config: VisibilityConfig | None = None,
) -> VisibilityEvaluation:
    """Solve a bounded isotropic geometry graph with physical endpoints.

    Nonburnable raster unions become clearance-expanded obstacles. Raster ROS
    interfaces also supply candidate nodes. All-pairs construction is O(N²),
    so oversized inputs fail explicitly instead of silently dropping nodes.
    """
    if config is None:
        config = VisibilityConfig()
    if not isinstance(scenario, Scenario) or not isinstance(config, VisibilityConfig):
        raise TypeError("scenario/config must use the declared research contracts")
    prepared = prepare_plan(grid, plan, profiles)
    edges = IntegratedEdges(grid, domain_mask, background_rates_m_s, prepared)
    nodes = {item.point_m for item in (*scenario.ignitions, *scenario.targets)}
    for point in nodes:
        if not edges.point_allowed(point):
            raise VisibilityPhysicalConflict(
                "ignition/target is outside the permitted physical domain"
            )
        if any(region.footprint.covers(Point(point)) for region in prepared.regions):
            raise VisibilityPhysicalConflict("physical ignition/target conflicts with treatment")
    obstacles = [
        r.footprint
        for index, r in enumerate(prepared.regions)
        if r.profile.mode == "M0" or edges.treated_rates[index] == 0
    ]
    for row, col in np.argwhere(~edges.domain):
        x, y = grid.centre(int(row), int(col))
        half = grid.cell_size_m / 2
        obstacles.append(box(x - half, y - half, x + half, y + half))
    obstacle_union = unary_union(obstacles)

    def add(point):
        if edges.point_allowed(point) and not obstacle_union.covers(Point(point)):
            nodes.add(point)
            if len(nodes) > config.max_nodes:
                raise VisibilityResourceLimit(
                    "visibility node budget exceeded; reduce domain or refine budget"
                )

    def sample_ring(coordinates, spacing):
        for a, b in pairwise(coordinates):
            count = ceil(hypot(b[0] - a[0], b[1] - a[1]) / spacing) if spacing else 1
            if count > config.max_nodes:
                raise VisibilityResourceLimit("visibility boundary sampling exceeds node budget")
            for k in range(count):
                t = k / count
                add((a[0] + t * (b[0] - a[0]), a[1] + t * (b[1] - a[1])))

    if not obstacle_union.is_empty:
        inflated = obstacle_union.buffer(config.clearance_m, join_style="mitre")
        for polygon in _polygons(inflated):
            for ring in (polygon.exterior, *polygon.interiors):
                sample_ring(list(ring.coords), None)
    for index, region in enumerate(prepared.regions):
        if region.profile.mode == "M1" and edges.treated_rates[index] != 0:
            # Boundary travel itself uses treated ROS. Without exterior corner
            # candidates, finite M1 strips could falsely suppress cheap end
            # bypasses. Add valid exterior paths without eroding treatments.
            outside = region.footprint.buffer(config.clearance_m, join_style="mitre")
            for polygon in _polygons(outside):
                for ring in (polygon.exterior, *polygon.interiors):
                    sample_ring(list(ring.coords), None)
            for polygon in _polygons(region.footprint):
                for ring in (polygon.exterior, *polygon.interiors):
                    sample_ring(list(ring.coords), config.boundary_spacing_m)
    # Include endpoints and midpoint of each rate-discontinuity cell face.
    for row, col in np.argwhere(edges.rates[:, 1:] != edges.rates[:, :-1]):
        x = grid.x_min_m + (int(col) + 1) * grid.cell_size_m
        y = grid.y_max_m - int(row) * grid.cell_size_m
        for fraction in (0.0, 0.5, 1.0):
            add((x, y - fraction * grid.cell_size_m))
    for row, col in np.argwhere(edges.rates[1:, :] != edges.rates[:-1, :]):
        x = grid.x_min_m + int(col) * grid.cell_size_m
        y = grid.y_max_m - (int(row) + 1) * grid.cell_size_m
        for fraction in (0.0, 0.5, 1.0):
            add((x + fraction * grid.cell_size_m, y))
    if len(nodes) > config.max_nodes:
        raise VisibilityResourceLimit("visibility node budget exceeded")
    points = tuple(sorted(nodes))
    lookup = {point: index for index, point in enumerate(points)}
    adjacency = [[] for _ in points]
    tested = finite = 0
    for i, source in enumerate(points):
        for j in range(i + 1, len(points)):
            travel = edges.travel_time_s(source, points[j])
            tested += 1
            if isfinite(travel):
                finite += 1
                adjacency[i].append((j, travel))
                adjacency[j].append((i, travel))
    arrival = [inf] * len(points)
    predecessor = [None] * len(points)
    queue = []
    for ignition in scenario.ignitions:
        index = lookup[ignition.point_m]
        if ignition.time_s < arrival[index]:
            arrival[index] = ignition.time_s
            heappush(queue, (ignition.time_s, index))
    while queue:
        time, index = heappop(queue)
        if time != arrival[index]:
            continue
        for target, travel in adjacency[index]:
            candidate = time + travel
            if candidate < arrival[target]:
                arrival[target] = candidate
                predecessor[target] = index
                heappush(queue, (candidate, target))
    targets = tuple(arrival[lookup[t.point_m]] for t in scenario.targets)
    paths = []
    for target in scenario.targets:
        index = lookup[target.point_m]
        if not isfinite(arrival[index]):
            paths.append(())
            continue
        path = []
        while index is not None:
            path.append(points[index])
            index = predecessor[index]
        paths.append(tuple(reversed(path)))
    exposed = sum(
        t.weight
        for t, time in zip(scenario.targets, targets, strict=True)
        if time <= scenario.horizon_s
    )
    return VisibilityEvaluation(
        plan.plan_id,
        scenario.scenario_id,
        points,
        tuple(arrival),
        targets,
        exposed,
        prepared.treated_area_m2,
        prepared.cost_units,
        len(points),
        tested,
        finite,
        config,
        tuple(paths),
    )
