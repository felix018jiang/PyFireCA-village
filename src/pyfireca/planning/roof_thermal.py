"""Independent polygon roof patches on the V3 thermal clock.

The 1 x N solver grid is storage only. Physical coordinates, roof identities
and contact topology come from this layout, never from storage adjacency.
Overlapping annotations share an explicitly ambiguous *physical* patch; their
fuel is counted once. Disjoint buildings never share a temperature state.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from math import atan, ceil, cos, floor, pi, radians, sin

import numpy as np
from shapely import points
from shapely.geometry import Point, box, mapping
from shapely.strtree import STRtree

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.buildings import _raster
from pyfireca.planning.interventions.clearing import EXTENSIVE, InterventionConflict
from pyfireca.planning.spatial import equivalent_assembly
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.physics_v3 import V3CellPhysicsArrays
from pyfireca.village.solver_v3 import V3SimulationRequest


def _polygons(geometry):
    if geometry.geom_type == "Polygon":
        return [geometry] if geometry.area > 0 else []
    return [p for g in getattr(geometry, "geoms", ()) for p in _polygons(g)]


def _atoms(entries):
    """Disjoint geometry atoms with all covering annotation IDs; no priority loss."""
    atoms = []
    for identifier, geometry in entries:
        remaining = geometry
        updated = []
        for owners, old in atoms:
            updated.extend((owners, p) for p in _polygons(old.difference(geometry)))
            updated.extend(
                (tuple(sorted((*owners, identifier))), p)
                for p in _polygons(old.intersection(geometry))
            )
            remaining = remaining.difference(old)
        updated.extend(((identifier,), p) for p in _polygons(remaining))
        atoms = updated
    return sorted(atoms, key=lambda item: (item[0], item[1].bounds, item[1].wkb_hex))


@dataclass(frozen=True)
class RoofNodeInputs:
    """Arrays without ignition; cannot be submitted to the thermal solver."""

    grid: VillageGridSpec
    parameters: V3CellPhysicsArrays
    exposure_edges: ExposureEdges
    domain_mask: np.ndarray
    known_mask: np.ndarray
    burnable_mask: np.ndarray
    source_mask: np.ndarray
    target_mask: np.ndarray
    piloted_ignition_mask: np.ndarray
    material_index: np.ndarray
    landcover_index: np.ndarray
    building_index: np.ndarray
    sample_interval_s: float | None
    unknown_policy: str

    def bind(self, scenario):
        return V3SimulationRequest(
            **{k: getattr(self, k) for k in self.__dataclass_fields__}, scenario=scenario
        )


@dataclass(frozen=True)
class RoofLayout:
    physical_request: V3SimulationRequest
    node_inputs: RoofNodeInputs
    cell_id: np.ndarray
    component_id: tuple[str, ...]
    owners: tuple[tuple[str, ...], ...]
    geometry: tuple
    xy_m: np.ndarray
    area_fraction: np.ndarray
    conservation: dict
    inventory_sha256: str
    ambiguous_ids: tuple[str, ...]
    node_request: V3SimulationRequest | None = None

    @property
    def thermal(self):
        return self.node_request if self.node_request is not None else self.node_inputs

    def nodes(self, building_id):
        return np.asarray([i for i, ids in enumerate(self.owners) if building_id in ids], int)

    @property
    def sha256(self):
        return content_hash(self.to_mapping())

    def to_mapping(self):
        return {
            "schema_version": "heritage-roof-layout-1",
            "physical_grid": self.physical_request.grid.__dict__
            if hasattr(self.physical_request.grid, "__dict__")
            else {
                k: getattr(self.physical_request.grid, k)
                for k in self.physical_request.grid.__dataclass_fields__
            },
            "storage_grid_is_geographic": False,
            "inventory_sha256": self.inventory_sha256,
            "ambiguous_annotation_ids": self.ambiguous_ids,
            "conservation_max_abs_error": self.conservation,
            "roof_components": [
                {
                    "node": i,
                    "component_id": self.component_id[i],
                    "physical_cell_id": int(self.cell_id[i]),
                    "building_ids": ids,
                    "xy_m": self.xy_m[i].tolist(),
                    "area_fraction_of_parent_roof": float(self.area_fraction[i]),
                    "geometry": mapping(self.geometry[i]),
                }
                for i, ids in enumerate(self.owners)
                if ids
            ],
            "nonroof_policy": "one_unchanged_parent_state_per_physical_cell",
        }


def resolve_roof_layout(request, inventory, *, max_nodes=200_000):
    """Partition parent roof budgets by vector area; keep nonroof cells unchanged."""
    if request.firebrands is not None:
        raise ValueError("independent roof layout does not support firebrands")
    _, rows, coverage, _ = _raster(inventory, request.grid)
    occupied = coverage > 0
    if not np.array_equal(occupied, request.building_index >= 0) or not np.allclose(
        coverage[occupied], request.parameters.coverage_fraction[occupied], rtol=1e-6, atol=1e-8
    ):
        raise ValueError("roof geometry is stale against physical case coverage")
    if np.any(occupied & ~request.burnable_mask):
        raise ValueError("roof inventory includes nonburnable parent cells")
    # This stage has no surveyed per-building assemblies. Do not allocate an
    # owner's heterogeneous material to a different house in a shared cell.
    equivalent_assembly(request, occupied)
    grid, by_cell = request.grid, {}
    for identifier, row, col, _ in rows:
        by_cell.setdefault(grid.cell_id(row, col), []).append(identifier)
    cells, names, owners, geometries, xy, fractions = [], [], [], [], [], []
    ambiguous = set()
    core_ids = {t.building_id for t in inventory.targets}
    domain = box(
        grid.x_min_m,
        grid.y_max_m - grid.height * grid.cell_size_m,
        grid.x_min_m + grid.width * grid.cell_size_m,
        grid.y_max_m,
    )
    if any(b.geometry.difference(domain).area > 1e-7 for b in inventory.buildings):
        raise ValueError("building is clipped by physical domain")
    for cell in range(grid.height * grid.width):
        row, col = divmod(cell, grid.width)
        x, y = grid.center(row, col)
        cell_geometry = box(
            x - grid.cell_size_m / 2,
            y - grid.cell_size_m / 2,
            x + grid.cell_size_m / 2,
            y + grid.cell_size_m / 2,
        )
        identifiers = sorted(set(by_cell.get(cell, ())))
        if not identifiers:
            parts = [((), cell_geometry)]
        else:
            parts = _atoms(
                [(i, inventory.by_id[i].geometry.intersection(cell_geometry)) for i in identifiers]
            )
        total = sum(g.area for _, g in parts)
        for part_index, (ids, geometry) in enumerate(parts):
            if len(ids) > 1:
                if core_ids.intersection(ids):
                    raise ValueError("core overlaps another annotation; semantic review required")
                ambiguous.update(ids)
            point = geometry.representative_point() if ids else Point(x, y)
            cells.append(cell)
            names.append(f"cell{cell}:{'+'.join(ids) if ids else 'ground'}:{part_index}")
            owners.append(ids)
            geometries.append(geometry)
            xy.append((point.x, point.y))
            fractions.append(geometry.area / total if ids else 1.0)
        if len(cells) > max_nodes:
            raise ValueError("roof component node limit exceeded")
    cells, fractions = np.asarray(cells, np.int32), np.asarray(fractions)
    fields, conservation = {}, {}
    for name in V3CellPhysicsArrays.__dataclass_fields__:
        old = getattr(request.parameters, name).ravel()
        values = old[cells].copy()
        if name in EXTENSIVE:
            values *= fractions
            restored = np.bincount(cells, weights=values, minlength=len(old))
            if not np.allclose(restored, old, rtol=1e-10, atol=1e-6):
                raise AssertionError(f"roof partition does not conserve {name}")
            conservation[name] = float(np.max(np.abs(restored - old)))
        fields[name] = values.reshape(1, -1)
    # Parent roof cells already contain a single equivalent material. Splitting
    # those arrays does not infer different timber properties from RGB labels.
    count = len(cells)
    masks = {
        name: getattr(request, name).ravel()[cells].reshape(1, -1).copy()
        for name in (
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
    for i, ids in enumerate(owners):
        if ids:
            masks["building_index"][0, i] = (
                inventory.by_id[ids[0]].object_id if len(ids) == 1 else -1
            )
    node_inputs = RoofNodeInputs(
        grid=VillageGridSpec(1, count, grid.cell_size_m, 0, grid.cell_size_m),
        parameters=V3CellPhysicsArrays(**fields),
        exposure_edges=ExposureEdges(
            np.array([], np.int32), np.array([], np.int32), np.array([], float), count
        ),
        sample_interval_s=request.sample_interval_s,
        unknown_policy=request.unknown_policy,
        **masks,
    )
    return RoofLayout(
        request,
        node_inputs,
        cells,
        tuple(names),
        tuple(owners),
        tuple(geometries),
        np.asarray(xy),
        fractions,
        conservation,
        inventory.sha256,
        tuple(sorted(ambiguous)),
    )


def roof_radiation_graph(layout, transport, *, max_edges=5_000_000):
    """Transparent regularized point radiation at actual patch positions.

    Reuses V3's point-source equation, including wind/height and atmospheric
    attenuation. This is an explicit geometric approximation, not finite roof
    view factors. Outgoing source budgets are checked, never renormalized.
    """
    request, xy = layout.thermal, layout.xy_m
    physics = request.parameters
    source_ids = np.flatnonzero(request.source_mask)
    target_ids = np.flatnonzero(request.target_mask)
    target_points = points(xy[target_ids])
    tree = STRtree(target_points)
    tilt = atan(transport.wind_speed_m_s * transport.flame_tilt_coefficient_s_m)
    # Match transport_v3: meteorological 'from' becomes downwind 'towards'.
    azimuth = radians((transport.wind_from_deg + 180) % 360)
    half = physics.source_height_m.ravel() / 2
    dx, dy = half * sin(tilt) * sin(azimuth), half * sin(tilt) * cos(azimuth)
    ground = layout.physical_request.ground_elevation_m
    z = np.zeros(len(xy)) if ground is None else ground.ravel()[layout.cell_id]
    source_z = z + half * cos(tilt)
    target_z = z + physics.receiver_height_m.ravel()
    sources, targets, weights, edge_count = [], [], [], 0
    for start in range(0, len(source_ids), 1024):
        ids = source_ids[start : start + 1024]
        pair = tree.query(
            points(xy[ids]), predicate="dwithin", distance=transport.exposure_radius_m
        )
        s, t = ids[pair[0]], target_ids[pair[1]]
        keep = s != t
        s, t = s[keep], t[keep]
        edge_count += len(s)
        if edge_count > max_edges:
            raise ValueError("roof radiation edge limit exceeded")
        distance2 = (
            (xy[t, 0] - xy[s, 0] - dx[s]) ** 2
            + (xy[t, 1] - xy[s, 1] - dy[s]) ** 2
            + (target_z[t] - source_z[s]) ** 2
        )
        denominator = distance2 + transport.near_field_regularization_m**2
        if np.any(denominator <= 0):
            raise ValueError("singular roof point-source distance")
        weight = physics.receiver_area_m2.ravel()[t] / (4 * pi * denominator)
        weight *= np.exp(-transport.atmospheric_extinction_m_inv * np.sqrt(distance2))
        sources.append(s)
        targets.append(t)
        weights.append(weight)
    return ExposureEdges(
        np.concatenate(sources).astype(np.int32) if sources else np.array([], np.int32),
        np.concatenate(targets).astype(np.int32) if targets else np.array([], np.int32),
        np.concatenate(weights) if weights else np.array([], float),
        len(xy),
    )


def _area_quadrature(geometry, spacing_m, *, x0, y0):
    """Fixed metric tiles represented by area-weighted interior points."""
    if geometry.area <= spacing_m**2 * 1e-8:
        point = geometry.representative_point()
        return np.asarray([(point.x, point.y)], float), np.ones(1)
    xmin, ymin, xmax, ymax = geometry.bounds
    samples, areas = [], []
    for row in range(floor((y0 - ymax) / spacing_m), ceil((y0 - ymin) / spacing_m)):
        for col in range(floor((xmin - x0) / spacing_m), ceil((xmax - x0) / spacing_m)):
            tile = box(
                x0 + col * spacing_m,
                y0 - (row + 1) * spacing_m,
                x0 + (col + 1) * spacing_m,
                y0 - row * spacing_m,
            )
            for part in _polygons(geometry.intersection(tile)):
                point = part.representative_point()
                samples.append((point.x, point.y))
                areas.append(part.area)
    areas = np.asarray(areas, float)
    if not len(areas) or not np.isclose(areas.sum(), geometry.area, rtol=1e-9, atol=1e-7):
        raise AssertionError("roof radiation quadrature changed geometry area")
    return np.asarray(samples, float), areas / areas.sum()


def roof_area_integrated_radiation_graph(
    layout,
    transport,
    *,
    integrated_ids,
    quadrature_spacing_m=0.5,
    max_edges=5_000_000,
):
    """Integrate radiation over selected roofs while retaining the V3 kernel.

    The heat-transfer equation, wind tilt, height treatment, attenuation and
    material parameters are unchanged. Selected roofs use a fixed metric area
    quadrature; every other node keeps its existing representative point. This
    local correction targets source and heritage roofs without remeshing the
    full village or replacing the validated background transport model.
    """
    selected = frozenset(integrated_ids)
    available = {identifier for ids in layout.owners for identifier in ids}
    if not selected or not selected <= available:
        raise ValueError("area-integrated radiation requires known selected roof IDs")
    if not np.isfinite(quadrature_spacing_m) or quadrature_spacing_m <= 0:
        raise ValueError("radiation quadrature spacing must be finite and positive")

    request, xy = layout.thermal, layout.xy_m
    physics = request.parameters
    source_mask = request.source_mask.ravel()
    target_mask = request.target_mask.ravel()
    integrated = np.asarray([bool(selected.intersection(ids)) for ids in layout.owners])

    # Keep the old graph exactly for pairs unrelated to the selected roofs.
    point_graph = roof_radiation_graph(layout, transport, max_edges=max_edges)
    keep = ~(integrated[point_graph.source] | integrated[point_graph.target])
    sources = [point_graph.source[keep]]
    targets = [point_graph.target[keep]]
    weights = [point_graph.weight[keep]]

    source_ids = np.flatnonzero(source_mask)
    target_ids = np.flatnonzero(target_mask)
    selected_sources = source_ids[integrated[source_ids]]
    ordinary_sources = source_ids[~integrated[source_ids]]
    selected_targets = target_ids[integrated[target_ids]]
    pair_blocks = []
    if len(selected_sources):
        tree = STRtree([layout.geometry[i] for i in target_ids])
        pair = tree.query(
            [layout.geometry[i] for i in selected_sources],
            predicate="dwithin",
            distance=transport.exposure_radius_m,
        )
        pair_blocks.append((selected_sources[pair[0]], target_ids[pair[1]]))
    if len(ordinary_sources) and len(selected_targets):
        tree = STRtree([layout.geometry[i] for i in selected_targets])
        pair = tree.query(
            [layout.geometry[i] for i in ordinary_sources],
            predicate="dwithin",
            distance=transport.exposure_radius_m,
        )
        pair_blocks.append((ordinary_sources[pair[0]], selected_targets[pair[1]]))

    tilt = atan(transport.wind_speed_m_s * transport.flame_tilt_coefficient_s_m)
    azimuth = radians((transport.wind_from_deg + 180) % 360)
    half = physics.source_height_m.ravel() / 2
    dx, dy = half * sin(tilt) * sin(azimuth), half * sin(tilt) * cos(azimuth)
    ground = layout.physical_request.ground_elevation_m
    z = np.zeros(len(xy)) if ground is None else ground.ravel()[layout.cell_id]
    source_z = z + half * cos(tilt)
    target_z = z + physics.receiver_height_m.ravel()
    receiver_area = physics.receiver_area_m2.ravel()
    grid = layout.physical_request.grid
    quadrature = {}

    def samples(node):
        if not integrated[node]:
            return xy[node : node + 1], np.ones(1)
        if node not in quadrature:
            quadrature[node] = _area_quadrature(
                layout.geometry[node],
                quadrature_spacing_m,
                x0=grid.x_min_m,
                y0=grid.y_max_m,
            )
        return quadrature[node]

    area_sources, area_targets, area_weights = [], [], []
    edge_count = int(keep.sum())
    for block_sources, block_targets in pair_blocks:
        for source, target in zip(block_sources, block_targets, strict=True):
            source, target = int(source), int(target)
            if source == target:
                continue
            source_xy, source_fraction = samples(source)
            target_xy, target_fraction = samples(target)
            horizontal2 = (target_xy[None, :, 0] - source_xy[:, None, 0]) ** 2 + (
                target_xy[None, :, 1] - source_xy[:, None, 1]
            ) ** 2
            within = horizontal2 <= transport.exposure_radius_m**2
            if not within.any():
                continue
            distance2 = (
                (target_xy[None, :, 0] - source_xy[:, None, 0] - dx[source]) ** 2
                + (target_xy[None, :, 1] - source_xy[:, None, 1] - dy[source]) ** 2
                + (target_z[target] - source_z[source]) ** 2
            )
            denominator = distance2 + transport.near_field_regularization_m**2
            if np.any(denominator <= 0):
                raise ValueError("singular roof area-source distance")
            kernel = np.exp(-transport.atmospheric_extinction_m_inv * np.sqrt(distance2)) / (
                4 * pi * denominator
            )
            fractions = source_fraction[:, None] * target_fraction[None, :]
            weight = receiver_area[target] * float(np.sum(kernel[within] * fractions[within]))
            if weight <= 0:
                continue
            area_sources.append(source)
            area_targets.append(target)
            area_weights.append(weight)
            edge_count += 1
            if edge_count > max_edges:
                raise ValueError("roof radiation edge limit exceeded")
    sources.append(np.asarray(area_sources, np.int32))
    targets.append(np.asarray(area_targets, np.int32))
    weights.append(np.asarray(area_weights, float))
    return ExposureEdges(
        np.concatenate(sources).astype(np.int32),
        np.concatenate(targets).astype(np.int32),
        np.concatenate(weights),
        len(xy),
    )


def roof_contact_operator(layout):
    """Bounded existing contact closure, gated by physical polygon contact.

    Same-cell or face-neighbor patches must share positive boundary length.
    Consequently a positive roof gap cannot create a contact edge. This gate
    does not model flames bridging a gap, facade geometry, or airflow.
    """
    parent, request = layout.physical_request, layout.thermal
    parameters = parent.flame_contact
    if parameters is None:
        return None
    if parent.grid.cell_size_m > parameters.max_cell_size_m + 1e-12:
        raise ValueError("roof contact exceeds configured maximum cell size")
    physics = request.parameters
    by_cell = {}
    for i, cell in enumerate(layout.cell_id):
        if request.burnable_mask[0, i]:
            by_cell.setdefault(int(cell), []).append(i)
    ground = parent.ground_elevation_m
    z = np.zeros(len(layout.cell_id)) if ground is None else ground.ravel()[layout.cell_id]
    heights = physics.receiver_height_m.ravel().copy()
    for material, height in parameters.receiver_height_overrides_m:
        heights[request.material_index.ravel() == material] = height
    pairs = []
    for cell, nodes in by_cell.items():
        row, col = divmod(cell, parent.grid.width)
        nearby = [cell]
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            rr, cc = row + dr, col + dc
            if 0 <= rr < parent.grid.height and 0 <= cc < parent.grid.width:
                nearby.append(rr * parent.grid.width + cc)
        for s in nodes:
            for other in nearby:
                for t in by_cell.get(other, ()):
                    if s == t:
                        continue
                    if layout.owners[s] or layout.owners[t]:
                        shared = layout.geometry[s].boundary.intersection(
                            layout.geometry[t].boundary
                        )
                        if shared.length <= 1e-9:
                            continue
                    base_s, base_t = z[s], z[t]
                    if parameters.terrain_reference == "shared_face" and cell != other:
                        base_s = base_t = 0.0
                    target_z = base_t + heights[t]
                    extent = parameters.receiver_vertical_half_extent_m
                    if extent > 0:
                        bottom, top = max(base_t, target_z - extent), target_z + extent
                        weight = max(
                            0.0,
                            min(top, base_s + physics.source_height_m[0, s]) - max(bottom, base_s),
                        ) / (top - bottom)
                    else:
                        weight = float(base_s <= target_z <= base_s + physics.source_height_m[0, s])
                    if weight > 0:
                        pairs.append((s, t, physics.coverage_fraction[0, s] * weight))
    size = max((int(x) for x in np.bincount([s for s, _, _ in pairs])), default=0)
    neighbors = np.full((len(layout.cell_id), size), -1, np.int32)
    geometry = np.zeros(neighbors.shape)
    cursor = np.zeros(len(layout.cell_id), int)
    for s, t, weight in pairs:
        neighbors[s, cursor[s]], geometry[s, cursor[s]] = t, weight
        cursor[s] += 1
    return FlameContactOperator(neighbors, geometry, parameters, physics)


def bind_roof_ignitions(layout, inventory, scenario, *, source_policy):
    """Bind points to a single physical roof patch, never to an entire coarse cell."""
    if source_policy not in {
        "technical_probe",
        "declared_fixed_sources",
        "sampled_retained_sources",
    }:
        raise ValueError("source policy must be explicit")
    events, source_ids = [], set()
    core_ids = {t.building_id for t in inventory.targets}
    roof_nodes = np.asarray([i for i, ids in enumerate(layout.owners) if ids], int)
    roof_tree = STRtree([layout.geometry[i] for i in roof_nodes])
    for ignition in scenario.ignitions:
        if ignition.time_s != 0:
            raise ValueError("P1 roof evaluation requires time-zero source ignitions")
        point = Point(*ignition.point_m)
        hits = roof_nodes[roof_tree.query(point, predicate="within")]
        if len(hits) != 1 or len(layout.owners[hits[0]]) != 1:
            raise ValueError("source point must be strictly inside one unambiguous roof patch")
        node = int(hits[0])
        identifier = layout.owners[node][0]
        if identifier in core_ids or inventory.by_id[identifier].source_label != "building":
            raise ValueError("transmission source must be an ordinary building, not a core")
        if not layout.thermal.source_mask[0, node]:
            raise ValueError("source patch must be an enabled emitter")
        source_ids.add(identifier)
        events.append(VillageIgnitionEvent(ignition.time_s, 0, node))
    if source_policy == "declared_fixed_sources" and (
        not inventory.fixed_source_ids or source_ids != set(inventory.fixed_source_ids)
    ):
        raise ValueError("source points must match all declared fixed source IDs")
    return tuple(events), tuple(sorted(source_ids))


def apply_roof_demolition(layout, inventory, plan, *, source_ids):
    """Clear only explicit eligible whole buildings; retained node budgets stay exact."""
    removed = set(plan.demolished_ids)
    if not removed <= set(inventory.candidate_ids):
        raise InterventionConflict("demolition requires declared ordinary candidate IDs")
    forbidden = (
        set(source_ids) | set(inventory.protected_ids) | {t.building_id for t in inventory.targets}
    )
    if removed.intersection(forbidden):
        raise InterventionConflict("cannot demolish heritage, protected building or active source")
    if removed.intersection(layout.ambiguous_ids) and plan.overlap_policy == "reject":
        raise InterventionConflict("overlapping annotations need semantic review before demolition")
    mask = np.asarray([bool(ids) and set(ids) <= removed for ids in layout.owners])
    shared = np.asarray([bool(removed.intersection(ids)) for ids in layout.owners]) & ~mask
    owners = tuple(
        tuple(identifier for identifier in ids if identifier not in removed)
        if shared[node]
        else ids
        for node, ids in enumerate(layout.owners)
    )
    request = layout.node_request
    if request is None:
        raise ValueError("bind explicit ignition before demolition")
    fields = {
        k: getattr(request.parameters, k).copy() for k in V3CellPhysicsArrays.__dataclass_fields__
    }
    removed_fuel = float(np.sum(request.parameters.initial_dry_fuel_mass_kg.ravel()[mask]))
    removed_energy = float(np.sum(request.parameters.available_energy_J.ravel()[mask]))
    for values in fields.values():
        values[0, mask] = 0
    masks = {
        k: getattr(request, k).copy()
        for k in (
            "burnable_mask",
            "source_mask",
            "target_mask",
            "piloted_ignition_mask",
            "material_index",
            "landcover_index",
            "building_index",
        )
    }
    for k, values in masks.items():
        values[0, mask] = -1 if k == "building_index" else 0
    for node in np.flatnonzero(shared):
        masks["building_index"][0, node] = (
            inventory.by_id[owners[node][0]].object_id if len(owners[node]) == 1 else -1
        )
    changed = replace(request, parameters=V3CellPhysicsArrays(**fields), **masks)
    ledger = {
        **plan.to_mapping(),
        "kind": "whole_building_demolition",
        "demolished_count": len(removed),
        "demolished_footprint_area_m2": sum(inventory.by_id[i].geometry.area for i in removed),
        "cleared_roof_union_area_m2": sum(layout.geometry[i].area for i in np.flatnonzero(mask)),
        "retained_shared_roof_area_m2": sum(
            layout.geometry[i].area for i in np.flatnonzero(shared)
        ),
        "overlap_ownership_survey_verified": False,
        "overlap_interpretation": (
            "retain_all_fuel_where_any_declared_roof_owner_survives;hypothetical_geometry_policy"
            if plan.overlap_policy == "retain_shared_roof"
            else "reject_ambiguous_demolition"
        ),
        "removed_dry_fuel_kg": removed_fuel,
        "removed_available_energy_J": removed_energy,
        "affected_household_count": None,
        "implementation_feasibility_verified": False,
        "candidate_evidence": inventory.candidate_evidence,
        "retained_component_parameters_unchanged": True,
        "post_demolition_ground": "cleared_inert_no_opaque_wall",
    }
    return replace(layout, node_request=changed, owners=owners), ledger
