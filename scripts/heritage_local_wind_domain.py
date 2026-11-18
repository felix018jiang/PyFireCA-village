"""Cell-aligned working windows for single-source, static-wind V3 roof comparisons.

The window is a computational subset of one physical parent grid. A boundary
audit is only meaningful for the same frozen partitions, quadrature, clearance
actions, ambient temperature and local-contact model as the full-domain run.
"""

from dataclasses import dataclass, replace
from math import ceil, floor, isfinite
from numbers import Integral

import numpy as np
from shapely.geometry import box
from shapely.ops import unary_union

from pyfireca.planning.heritage import BuildingInventory
from pyfireca.planning.interventions.buildings import _raster
from pyfireca.planning.roof_thermal import _polygons
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v2 import monotone_thermal_timestep_limit_s
from pyfireca.village.workflow_v3 import V3CaseBundle


@dataclass(frozen=True)
class LocalWindDomain:
    bundle: V3CaseBundle
    inventory: BuildingInventory
    parent_grid: VillageGridSpec
    window: tuple[int, int, int, int]
    physical_boundary_mask: np.ndarray
    ambient_exterior_cfl_s: float
    exterior_can_ignite_at_ambient: bool

    def to_local_cell(self, global_id):
        if isinstance(global_id, bool) or not isinstance(global_id, Integral):
            raise TypeError("global cell ID must be an integer")
        row, col = self.parent_grid.row_col(int(global_id))
        r0, r1, c0, c1 = self.window
        if r0 <= row < r1 and c0 <= col < c1:
            return self.bundle.request.grid.cell_id(row - r0, col - c0)
        return None

    def to_global_cell(self, local_id):
        if isinstance(local_id, bool) or not isinstance(local_id, Integral):
            raise TypeError("local cell ID must be an integer")
        row, col = self.bundle.request.grid.row_col(int(local_id))
        r0, _, c0, _ = self.window
        return self.parent_grid.cell_id(row + r0, col + c0)

    def boundary_audit(self, ignited_local_cell_ids, exposure_radius_m, *, max_dt_s=None):
        """Audit all *ever* ignited physical cells, including earlier burned-out cells.

        Distances use the outer edges of the physical 5 m (or other native)
        cells, conservatively enclosing all roof fragments and quadrature points.
        One additional parent-cell width provides a contact/numerical margin.
        The original study boundary is reported independently of crop closure.
        """
        values = np.asarray(ignited_local_cell_ids)
        grid = self.bundle.request.grid
        if not values.size or values.dtype.kind not in "iu":
            raise ValueError("expected the integer physical IDs of all ever-ignited cells")
        if np.min(values) < 0 or np.max(values) >= grid.width * grid.height:
            raise ValueError("historical ignitions contain an out-of-window cell")
        cells = np.unique(values)
        row, col = np.divmod(cells, grid.width)
        if np.any(~self.bundle.request.domain_mask[row, col]):
            raise ValueError("historical ignitions include an inactive domain cell")
        if not isfinite(exposure_radius_m) or exposure_radius_m < 0:
            raise ValueError("radiation support must be finite and nonnegative")
        if max_dt_s is None:
            max_dt_s = self.bundle.request.scenario.max_dt_s
        if not isfinite(max_dt_s) or max_dt_s <= 0:
            raise ValueError("simulation maximum timestep must be finite and positive")

        r0, r1, c0, c1 = self.window
        side_distances = (
            (r0 > 0, int(row.min())),
            (r1 < self.parent_grid.height, grid.height - 1 - int(row.max())),
            (c0 > 0, int(col.min())),
            (c1 < self.parent_grid.width, grid.width - 1 - int(col.max())),
        )
        artificial_distances = [
            n * grid.cell_size_m for artificial, n in side_distances if artificial
        ]
        artificial_min = min(artificial_distances, default=None)
        guard = exposure_radius_m + grid.cell_size_m
        support_clear = artificial_min is None or artificial_min > guard
        cfl_clear = self.ambient_exterior_cfl_s >= max_dt_s
        return {
            "closed": bool(support_clear and cfl_clear and not self.exterior_can_ignite_at_ambient),
            "window_parent_indices": list(self.window),
            "artificial_boundary_min_gap_m": artificial_min,
            "required_artificial_gap_strictly_over_m": guard,
            "artificial_boundary_clear": support_clear,
            "original_physical_boundary_reached": bool(self.physical_boundary_mask[row, col].any()),
            "exterior_cfl_s": (
                self.ambient_exterior_cfl_s if isfinite(self.ambient_exterior_cfl_s) else None
            ),
            "exterior_cfl_allows_max_dt": bool(cfl_clear),
            "exterior_can_ignite_at_ambient": self.exterior_can_ignite_at_ambient,
        }


def _window_edges(edges, parent_grid, window):
    """Select sorted source-row ranges and preserve every incoming internal edge."""
    r0, r1, c0, c1 = window
    width = c1 - c0
    sources, targets, weights = [], [], []
    starts = np.arange(r0, r1) * parent_grid.width
    left = np.searchsorted(edges.source, starts + c0)
    right = np.searchsorted(edges.source, starts + c1)
    for row, (a, b) in enumerate(zip(left, right, strict=True)):
        destination = edges.target[a:b]
        rr, cc = np.divmod(destination, parent_grid.width)
        keep = (rr >= r0) & (rr < r1) & (cc >= c0) & (cc < c1)
        sources.append(
            (edges.source[a:b][keep] - (starts[row] + c0) + row * width).astype(np.int32)
        )
        targets.append(((rr[keep] - r0) * width + cc[keep] - c0).astype(np.int32))
        weights.append(edges.weight[a:b][keep])
    return ExposureEdges(
        np.concatenate(sources), np.concatenate(targets), np.concatenate(weights), width * (r1 - r0)
    )


def _clip_inventory(inventory, bounds):
    buildings = []
    for building in inventory.buildings:
        geometry = building.geometry
        if not bounds.covers(geometry):
            parts = _polygons(geometry.intersection(bounds))
            if not parts:
                continue
            geometry = unary_union(parts)
        buildings.append(replace(building, geometry=geometry))
    present = {b.building_id for b in buildings}
    return replace(
        inventory,
        buildings=tuple(buildings),
        targets=tuple(t for t in inventory.targets if t.building_id in present),
        protected_ids=tuple(i for i in inventory.protected_ids if i in present),
        fixed_source_ids=tuple(i for i in inventory.fixed_source_ids if i in present),
        candidate_ids=tuple(i for i in inventory.candidate_ids if i in present),
    )


def crop_wind_domain(bundle, inventory, center_xy, radius_m, *, ignition_xy=None):
    """Extract a square in world metres, rounded outward to complete parent cells.

    The physical request is bound to one time-zero direct source at ignition_xy
    (center_xy by default). A roof study can subsequently bind its declared
    ignition polygon on an independently resolved layout.
    """
    parent = bundle.request
    if parent.firebrands is not None:
        raise ValueError("this window audit requires bounded propagation without firebrands")
    if not isfinite(radius_m) or radius_m <= 0:
        raise ValueError("window radius must be positive and finite")
    grid = parent.grid
    x, y = center_xy
    grid.locate(x, y)
    size = grid.cell_size_m
    r0 = max(0, floor((grid.y_max_m - y - radius_m) / size))
    r1 = min(grid.height, ceil((grid.y_max_m - y + radius_m) / size))
    c0 = max(0, floor((x - grid.x_min_m - radius_m) / size))
    c1 = min(grid.width, ceil((x - grid.x_min_m + radius_m) / size))
    window = (r0, r1, c0, c1)
    local_grid = replace(
        grid,
        height=r1 - r0,
        width=c1 - c0,
        x_min_m=grid.x_min_m + c0 * size,
        y_max_m=grid.y_max_m - r0 * size,
    )
    bounds = box(
        local_grid.x_min_m,
        grid.y_max_m - r1 * size,
        grid.x_min_m + c1 * size,
        local_grid.y_max_m,
    )
    local_inventory = _clip_inventory(inventory, bounds)
    row, col = local_grid.locate(*(center_xy if ignition_xy is None else ignition_xy))
    sl = np.s_[r0:r1, c0:c1]
    parameters = replace(
        parent.parameters,
        **{
            name: getattr(parent.parameters, name)[sl]
            for name in parent.parameters.__dataclass_fields__
        },
    )
    names = (
        "domain_mask",
        "known_mask",
        "burnable_mask",
        "source_mask",
        "target_mask",
        "piloted_ignition_mask",
        "material_index",
        "landcover_index",
        "building_index",
        "ground_elevation_m",
        "material_evidence_index",
    )
    fields = {
        name: value[sl] if (value := getattr(parent, name)) is not None else None for name in names
    }
    cropped = replace(
        parent,
        grid=local_grid,
        parameters=parameters,
        scenario=replace(parent.scenario, ignitions=(VillageIgnitionEvent(0, row, col),)),
        exposure_edges=_window_edges(parent.exposure_edges, grid, window),
        **fields,
    )
    _, _, coverage, _ = _raster(local_inventory, local_grid)
    occupied = cropped.building_index >= 0
    if not np.array_equal(coverage > 0, occupied) or not np.allclose(
        coverage[occupied],
        cropped.parameters.coverage_fraction[occupied],
        rtol=1e-6,
        atol=1e-8,
    ):
        raise ValueError("clipped physical roof coverage disagrees with the original raster")
    metadata = dict(bundle.metadata, building_labels=local_inventory.labels, parent_window=window)
    local_bundle = replace(bundle, request=cropped, metadata=metadata)

    # Cold exterior nodes with zero incident power stay at their initial state.
    # The local clock matches the full clock if their cooling CFL is nonbinding.
    outside = parent.burnable_mask.copy()
    outside[sl] = False
    p = parent.parameters
    ambient = parent.scenario.ambient_temperature_K
    contact_h = parent.flame_contact.heat_transfer_coefficient_W_m2_K if parent.flame_contact else 0
    exterior_cfl = monotone_thermal_timestep_limit_s(
        np.full(int(outside.sum()), ambient),
        p.effective_heated_dry_mass_kg[outside] / p.covered_area_m2[outside],
        p.specific_heat_J_kg_K[outside],
        p.emissivity[outside],
        p.ambient_heat_transfer_coefficient_W_m2_K[outside] + contact_h,
        p.backing_conductance_W_m2_K[outside],
    )
    can_ignite = bool(
        np.any(outside & parent.piloted_ignition_mask & (p.ignition_temperature_K <= ambient))
    )
    # Edges of the parent domain include holes as well as the outer grid edge.
    padded = np.pad(parent.domain_mask, 1, constant_values=False)
    physical_boundary = parent.domain_mask[sl] & ~(
        padded[r0:r1, c0 + 1 : c1 + 1]
        & padded[r0 + 2 : r1 + 2, c0 + 1 : c1 + 1]
        & padded[r0 + 1 : r1 + 1, c0:c1]
        & padded[r0 + 1 : r1 + 1, c0 + 2 : c1 + 2]
    )
    return LocalWindDomain(
        local_bundle, local_inventory, grid, window, physical_boundary, exterior_cfl, can_ignite
    )
