"""Grid-independent technical ignition areas and local roof-scale experiments.

These are prescribed initial fires, not measured ignition probabilities. A
polygon is frozen in metres before meshing; intersecting a cell does not ignite
the rest of that cell. The parent scene and ground resolution remain unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor

import numpy as np
from shapely import boundary, intersection, length
from shapely.geometry import Point, box, mapping
from shapely.ops import unary_union
from shapely.strtree import STRtree

from pyfireca.planning.contracts import Ignition, require_id
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.interventions.clearing import EXTENSIVE
from pyfireca.planning.roof_thermal import RoofLayout, RoofNodeInputs, _polygons
from pyfireca.village.domain import ExposureEdges, VillageGridSpec
from pyfireca.village.flame_contact_v3 import FlameContactOperator
from pyfireca.village.physics_v3 import V3CellPhysicsArrays


def unambiguous_roof(inventory, source_id):
    if source_id not in inventory.by_id:
        raise ValueError("unknown technical source building")
    if (
        source_id in {t.building_id for t in inventory.targets}
        or inventory.by_id[source_id].source_label != "building"
    ):
        raise ValueError("technical source must be an ordinary building")
    source = inventory.by_id[source_id].geometry
    others = [
        b.geometry
        for b in inventory.buildings
        if b.building_id != source_id and b.geometry.intersects(source)
    ]
    roof = source.difference(unary_union(others)) if others else source
    if roof.is_empty or roof.area <= 0:
        raise ValueError("source has no unambiguous roof area")
    return roof


@dataclass(frozen=True)
class RoofIgnitionFootprint:
    source_id: str
    geometry: object
    provenance: str

    def __post_init__(self):
        require_id("source_id", self.source_id)
        require_id("provenance", self.provenance)
        if (
            self.geometry.geom_type not in {"Polygon", "MultiPolygon"}
            or not self.geometry.is_valid
            or self.geometry.area <= 0
        ):
            raise ValueError("ignition footprint requires a valid positive polygon")

    def to_mapping(self):
        return {
            "source_id": self.source_id,
            "geometry": mapping(self.geometry),
            "area_m2": float(self.geometry.area),
            "provenance": self.provenance,
        }

    @property
    def sha256(self):
        return content_hash(self.to_mapping())


def fixed_area_ignition(inventory, source_id, anchor_m, area_m2):
    """Nested disk/roof intersections of exact area; 'all' is a separate stress case."""
    roof = unambiguous_roof(inventory, source_id)
    anchor = Point(*anchor_m)
    if not roof.contains(anchor):
        raise ValueError("anchor must be strictly inside the unambiguous source roof")
    if area_m2 == "all":
        geometry = roof
    else:
        if isinstance(area_m2, bool) or not np.isfinite(area_m2) or not 0 < area_m2 <= roof.area:
            raise ValueError("requested source area must be positive and fit the unambiguous roof")
        lo, hi = 0.0, max(1.0, np.sqrt(area_m2))
        while anchor.buffer(hi, quad_segs=64).intersection(roof).area < area_m2:
            hi *= 2
        for _ in range(70):
            middle = (lo + hi) / 2
            if anchor.buffer(middle, quad_segs=64).intersection(roof).area < area_m2:
                lo = middle
            else:
                hi = middle
        geometry = anchor.buffer(hi, quad_segs=64).intersection(roof)
        if abs(geometry.area - area_m2) > 1e-7:
            raise AssertionError("fixed ignition area construction failed")
    return RoofIgnitionFootprint(
        source_id,
        geometry,
        f"technical_prescribed_area:{area_m2};fixed_anchor_m:{tuple(anchor_m)};"
        "unambiguous_roof_intersection;not_surveyed_or_fixed_study_source",
    )


@dataclass(frozen=True)
class RefinedRoofLayout(RoofLayout):
    support_area_m2: np.ndarray | None = None
    ignition_owner: tuple[str | None, ...] = ()
    resolution_protocol: dict | None = None

    def to_mapping(self):
        result = super().to_mapping()
        result["resolution_protocol"] = self.resolution_protocol
        for part in result["roof_components"]:
            node = part["node"]
            part["support_area_m2"] = float(self.support_area_m2[node])
            part["prescribed_ignition_source_id"] = self.ignition_owner[node]
        return result


def refine_roof_layout(
    layout,
    inventory,
    *,
    cell_size_m,
    refined_ids,
    footprints,
    phase_fraction=(0.0, 0.0),
    max_nodes=200_000,
):
    """Refine selected roofs; split exactly at fixed ignition polygon boundaries.

    Other buildings, vegetation, terrain samples and parent boundaries are held
    fixed. This is a local roof-component sensitivity test, not global DEM or
    full-domain grid convergence. The storage cell area is never a contact area.
    """
    if layout.node_request is not None:
        raise ValueError("refine an unbound baseline, before ignition or demolition")
    grid = layout.physical_request.grid
    if not np.isfinite(cell_size_m) or not 0 < cell_size_m <= grid.cell_size_m:
        raise ValueError("local roof cell size must be positive and no larger than parent")
    if (
        len(phase_fraction) != 2
        or any(not np.isfinite(x) or not 0 <= x < 1 for x in phase_fraction)
        or not set(refined_ids) <= inventory.by_id.keys()
    ):
        raise ValueError("invalid phase or refinement IDs")
    for footprint in footprints:
        roof = unambiguous_roof(inventory, footprint.source_id)
        if footprint.geometry.difference(roof).area > 1e-7:
            raise ValueError("ignition footprint extends beyond unambiguous source roof")
        if footprint.source_id not in refined_ids:
            raise ValueError("source roof must belong to the refinement set")
    for i, footprint in enumerate(footprints):
        if any(
            footprint.geometry.intersection(other.geometry).area > 1e-8 for other in footprints[:i]
        ):
            raise ValueError("prescribed ignition footprints may not overlap")
    old_nodes, geometries, owners, names, support, seed, fractions = [], [], [], [], [], [], []
    x0 = grid.x_min_m + phase_fraction[0] * cell_size_m
    y0 = grid.y_max_m + phase_fraction[1] * cell_size_m
    for old, (ids, geometry) in enumerate(zip(layout.owners, layout.geometry, strict=True)):
        if set(ids).intersection(refined_ids):
            xmin, ymin, xmax, ymax = geometry.bounds
            pieces = []
            for rr in range(floor((y0 - ymax) / cell_size_m), ceil((y0 - ymin) / cell_size_m)):
                for cc in range(floor((xmin - x0) / cell_size_m), ceil((xmax - x0) / cell_size_m)):
                    tile = box(
                        x0 + cc * cell_size_m,
                        y0 - (rr + 1) * cell_size_m,
                        x0 + (cc + 1) * cell_size_m,
                        y0 - rr * cell_size_m,
                    )
                    pieces.extend(_polygons(geometry.intersection(tile)))
            parts = [(p, None) for p in pieces]
            for footprint in footprints:
                if footprint.source_id not in ids:
                    continue
                updated = []
                for p, direct in parts:
                    updated.extend((q, direct) for q in _polygons(p.difference(footprint.geometry)))
                    hits = _polygons(p.intersection(footprint.geometry))
                    if hits and ids != (footprint.source_id,):
                        if sum(q.area for q in hits) > 1e-8:
                            raise ValueError("cannot force ambiguous roof ignition")
                        updated.extend((q, None) for q in hits)
                    else:
                        updated.extend((q, footprint.source_id) for q in hits)
                parts = updated
            area = cell_size_m**2
        else:
            parts, area = [(geometry, None)], grid.cell_area_m2
        parts.sort(key=lambda p: (p[0].bounds, p[0].wkb_hex, p[1] or ""))
        total = sum(p.area for p, _ in parts)
        if not np.isclose(total, geometry.area, rtol=1e-9, atol=1e-7):
            raise AssertionError("refinement changed physical geometry area")
        for j, (p, direct) in enumerate(parts):
            old_nodes.append(old)
            geometries.append(p)
            owners.append(ids)
            names.append(f"{layout.component_id[old]}/local{j}")
            support.append(area)
            seed.append(direct)
            fractions.append(p.area / total)
        if len(old_nodes) > max_nodes:
            raise ValueError("refined roof node limit exceeded")
    old_nodes, fractions = np.asarray(old_nodes, int), np.asarray(fractions)
    inputs = layout.node_inputs
    fields, closure = {}, {}
    for name in V3CellPhysicsArrays.__dataclass_fields__:
        old = getattr(inputs.parameters, name).ravel()
        values = old[old_nodes].copy()
        if name in EXTENSIVE:
            values *= fractions
            restored = np.bincount(old_nodes, weights=values, minlength=len(old))
            if not np.allclose(restored, old, rtol=1e-9, atol=1e-6):
                raise AssertionError(f"refinement failed to conserve {name}")
            closure[name] = float(np.max(abs(restored - old)))
        fields[name] = values.reshape(1, -1)
    count = len(old_nodes)
    arrays = {
        name: getattr(inputs, name).ravel()[old_nodes].reshape(1, -1)
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
    node_inputs = RoofNodeInputs(
        grid=VillageGridSpec(1, count, grid.cell_size_m, 0, grid.cell_size_m),
        parameters=V3CellPhysicsArrays(**fields),
        exposure_edges=ExposureEdges(
            np.array([], np.int32), np.array([], np.int32), np.array([], float), count
        ),
        sample_interval_s=inputs.sample_interval_s,
        unknown_policy=inputs.unknown_policy,
        **arrays,
    )
    protocol = {
        "schema_version": "local-roof-refinement-1",
        "cell_size_m": cell_size_m,
        "parent_ground_grid_m": grid.cell_size_m,
        "refined_ids": sorted(refined_ids),
        "phase_fraction": phase_fraction,
        "footprints": [f.to_mapping() for f in footprints],
        "ignition_geometry_sha256": content_hash([f.to_mapping() for f in footprints]),
        "contact_coverage_policy": "covered_area_divided_by_local_support_area",
        "background_scene": "full_parent_domain_no_crop",
        "refinement_conservation_max_abs_error": closure,
    }
    xy = np.asarray(
        [
            (p.representative_point().x, p.representative_point().y)
            if ids
            else tuple(layout.xy_m[old])
            for p, ids, old in zip(geometries, owners, old_nodes, strict=True)
        ]
    )
    return RefinedRoofLayout(
        physical_request=layout.physical_request,
        node_inputs=node_inputs,
        cell_id=layout.cell_id[old_nodes],
        component_id=tuple(names),
        owners=tuple(owners),
        geometry=tuple(geometries),
        xy_m=xy,
        area_fraction=layout.area_fraction[old_nodes] * fractions,
        conservation=layout.conservation,
        inventory_sha256=layout.inventory_sha256,
        ambiguous_ids=layout.ambiguous_ids,
        support_area_m2=np.asarray(support),
        ignition_owner=tuple(seed),
        resolution_protocol=protocol,
    )


def footprint_ignitions(layout):
    events = []
    for p, direct in zip(layout.geometry, layout.ignition_owner, strict=True):
        if direct is not None:
            point = p.representative_point()
            events.append(Ignition((point.x, point.y), 0.0))
    if not events:
        raise ValueError("no roof nodes inside prescribed ignition area")
    return tuple(events)


def refined_contact_operator(layout):
    """Same bounded heat closure with local support, independent of storage shape."""
    parent, request = layout.physical_request, layout.thermal
    parameters = parent.flame_contact
    if parameters is None:
        return None
    if parent.grid.cell_size_m > parameters.max_cell_size_m:
        raise ValueError("contact support exceeds configured limit")
    nodes = np.flatnonzero(request.burnable_mask)
    polygons = np.asarray(layout.geometry, dtype=object)[nodes]
    tree = STRtree(polygons)
    candidates = tree.query(polygons, predicate="intersects")
    s, t = nodes[candidates[0]], nodes[candidates[1]]
    keep = s != t
    s, t = s[keep], t[keep]
    sr, sc = np.divmod(layout.cell_id[s], parent.grid.width)
    tr, tc = np.divmod(layout.cell_id[t], parent.grid.width)
    keep = abs(sr - tr) + abs(sc - tc) <= 1
    s, t = s[keep], t[keep]
    shapes = np.asarray(layout.geometry, dtype=object)
    shared = length(intersection(boundary(shapes[s]), boundary(shapes[t])))
    keep = shared > 1e-9
    s, t = s[keep], t[keep]
    physics = request.parameters
    ground = parent.ground_elevation_m
    z = np.zeros(len(layout.cell_id)) if ground is None else ground.ravel()[layout.cell_id]
    source_z, target_z = z[s].copy(), z[t].copy()
    if parameters.terrain_reference == "shared_face":
        across = layout.cell_id[s] != layout.cell_id[t]
        source_z[across] = target_z[across] = 0.0
    heights = physics.receiver_height_m.ravel().copy()
    for material, height in parameters.receiver_height_overrides_m:
        heights[request.material_index.ravel() == material] = height
    receiver = target_z + heights[t]
    extent = parameters.receiver_vertical_half_extent_m
    if extent > 0:
        bottom, top = np.maximum(target_z, receiver - extent), receiver + extent
        vertical = np.maximum(
            0.0,
            np.minimum(top, source_z + physics.source_height_m.ravel()[s])
            - np.maximum(bottom, source_z),
        ) / (top - bottom)
    else:
        vertical = (
            (source_z <= receiver) & (receiver <= source_z + physics.source_height_m.ravel()[s])
        ).astype(float)
    coverage = physics.covered_area_m2.ravel() / layout.support_area_m2
    if np.any(coverage > 1 + 1e-6):
        raise ValueError("roof area exceeds local contact support")
    fraction = np.minimum(1.0, coverage[s]) * vertical
    keep = fraction > 0
    s, t, fraction = s[keep], t[keep], fraction[keep]
    order = np.lexsort((t, s))
    s, t, fraction = s[order], t[order], fraction[order]
    counts = np.bincount(s, minlength=len(layout.cell_id))
    width = int(counts.max(initial=0))
    neighbors = np.full((len(counts), width), -1, np.int32)
    geometry = np.zeros(neighbors.shape)
    slots = np.arange(len(s)) - np.repeat(np.cumsum(counts) - counts, counts)
    neighbors[s, slots], geometry[s, slots] = t, fraction
    return FlameContactOperator(neighbors, geometry, parameters, physics)
