"""Independent whole-parent bands around a union of protected/source roof polygons.

Keeps the complete polygons instead of filling the convex hull between them.
Existing generators and their bound artifacts remain unchanged.
"""

from types import SimpleNamespace

import numpy as np
import shapely
from prepare_heritage_all_roof_bands import conservative_partition_check, parent_cell_polygons
from shapely.geometry import box, mapping

from pyfireca.planning.area_candidates import build_cleared_area_function


def generate_protected_union_bands(
    parent,
    base,
    inventory,
    *,
    seed_ids,
    actual_source_ids,
    radius_m,
    offsets=(10, 20, 30),
    width_m=20,
):
    if parent.grid.cell_size_m != 5:
        raise ValueError("balanced bands require original complete 5 m parent cells")
    protected = {t.building_id for t in inventory.targets} | set(actual_source_ids)
    if set(seed_ids) & protected:
        raise ValueError("the original seed removes a protected core or an actual source")
    anchors = shapely.union_all([inventory.by_id[i].geometry for i in sorted(protected)])
    hull = anchors  # Explicit union; no convex hull and no intervening grass enclosure.
    all_roofs = shapely.union_all([b.geometry for b in inventory.buildings])
    cells = parent_cell_polygons(parent.grid)
    roof_cells = np.unique(
        [int(c) for c, owners in zip(base.cell_id, base.owners, strict=True) if owners]
    )
    eligible = (
        parent.domain_mask
        & parent.known_mask
        & parent.burnable_mask
        & (parent.building_index < 0)
        & (parent.parameters.covered_area_m2 > 0)
    ).ravel()
    eligible[roof_cells] = False
    shapes = np.asarray(base.geometry, dtype=object)
    roof_nodes = np.asarray([bool(ids) for ids in base.owners])
    area = build_cleared_area_function(
        base.owners,
        [g.area for g in base.geometry],
        area_basis="original_disjoint_roof_geometry;all_owners_removed",
    )
    raw_potential = np.logical_or.reduce(
        [
            np.asarray(getattr(base.node_inputs, name)).ravel()
            for name in ("burnable_mask", "source_mask", "target_mask")
        ]
    )
    domain = box(
        parent.grid.x_min_m,
        parent.grid.y_max_m - parent.grid.height * 5,
        parent.grid.x_min_m + parent.grid.width * 5,
        parent.grid.y_max_m,
    )
    plans, features = [], []
    for offset in offsets:
        if offset < 0 or width_m <= 0:
            raise ValueError("invalid compact ring offset or width")
        inner, outer = (
            hull.buffer(offset, quad_segs=64),
            hull.buffer(offset + width_m, quad_segs=64),
        )
        ring = outer.difference(inner)
        shapely.prepare(ring)
        roof_hits = roof_nodes & shapely.intersects(shapes, ring)
        roof_overlap = np.zeros(len(shapes))
        roof_overlap[roof_hits] = shapely.area(shapely.intersection(shapes[roof_hits], ring))
        ring_roof_nodes = np.flatnonzero(roof_overlap > 1e-8)
        requested_owners = {
            identifier for node in ring_roof_nodes for identifier in base.owners[node]
        }
        removed = tuple(sorted(set(seed_ids) | (requested_owners - protected)))
        roof_cleared = np.asarray([bool(ids) and set(ids) <= set(removed) for ids in base.owners])
        grass_ids = np.flatnonzero(eligible & shapely.intersects(cells, ring))
        grass_geometry = shapely.union_all(cells[grass_ids])
        if grass_geometry.intersection(all_roofs).area > 1e-8:
            raise ValueError("compact grass footprints overlap actual inventory roofs")
        roof_geometry = shapely.union_all(shapes[roof_cleared])
        roof_cost = float(area(removed))
        grass_cost = len(grass_ids) * parent.grid.cell_area_m2
        construction_union = roof_geometry.union(grass_geometry)
        if not np.isclose(construction_union.area, roof_cost + grass_cost, atol=1e-6, rtol=0):
            raise ValueError("compact roof and ground action cost is not a disjoint union")
        masks = {}
        for name in ("burnable_mask", "source_mask", "target_mask"):
            value = np.asarray(getattr(base.node_inputs, name)).copy()
            value.ravel()[roof_cleared] = False
            masks[name] = value
        post_roof = SimpleNamespace(
            node_inputs=SimpleNamespace(**masks), cell_id=base.cell_id, geometry=base.geometry
        )
        cut = conservative_partition_check(post_roof, grass_ids, inner, radius_m)
        cut["potential_node_mask"] = (
            "original_burnable_or_source_or_target;actual_whole_owner_AND_roof_and_ground_clearance"
        )
        remaining = raw_potential & ~roof_cleared & ~np.isin(base.cell_id, grass_ids)
        remaining_shapes = shapes[remaining]
        leakage = shapely.area(
            shapely.intersection(remaining_shapes[shapely.intersects(remaining_shapes, ring)], ring)
        )
        counts = {"fully_cleared_ids": [], "partially_cleared_ids": []}
        for identifier in removed:
            owned = np.asarray([identifier in ids for ids in base.owners])
            key = "fully_cleared_ids" if np.all(roof_cleared[owned]) else "partially_cleared_ids"
            counts[key].append(identifier)
        ring_hit = shapely.intersects(cells, ring)
        plan_id = f"roof-seed-protected-union-ring-b{offset:g}-w{width_m:g}"
        plans.append(
            {
                "plan_id": plan_id,
                "demolished_ids": list(removed),
                "cleared_physical_cell_ids": grass_ids.tolist(),
                "hull_anchor_ids": sorted(protected),
                "inner_geometry_basis": "union_full_core_and_actual_source_roofs;no_convex_hull",
                "protected_union_area_m2": float(anchors.area),
                "protected_union_component_count": len(shapely.get_parts(anchors)),
                "inner_hull_buffer_m": offset,
                "nominal_ring_width_m": width_m,
                "buffer_quad_segs": 64,
                "roof_selection": (
                    "positive_area_ring_intersection_of_original_patch;"
                    "all_ordinary_owners;whole_ID_actions"
                ),
                "roof_positive_overlap_tolerance_m2": 1e-8,
                "seed_ids_retained_in_actions": list(seed_ids),
                "ring_intersected_physical_roof_node_count": len(ring_roof_nodes),
                "ring_requested_ordinary_owner_ids": sorted(requested_owners - protected),
                "ring_unremovable_protected_owner_ids": sorted(requested_owners & protected),
                "planned_roof_ID_count": len(removed),
                **counts,
                "cleared_roof_physical_node_count": int(roof_cleared.sum()),
                "cleared_roof_geometry_union_area_m2": roof_cost,
                "cleared_roof_covered_fuel_area_m2": float(
                    base.node_inputs.parameters.covered_area_m2.ravel()[roof_cleared].sum()
                ),
                "cleared_vegetation_parent_cell_count": len(grass_ids),
                "cleared_vegetation_ground_footprint_area_m2": grass_cost,
                "cleared_vegetation_covered_fuel_area_m2": float(
                    parent.parameters.covered_area_m2.ravel()[grass_ids].sum()
                ),
                "C_construction_action_area_m2": roof_cost + grass_cost,
                "nominal_ring_geometry_area_m2": float(ring.area),
                "nominal_ring_outside_parent_rectangle_area_m2": float(
                    ring.difference(domain).area
                ),
                "unknown_parent_cells_intersecting_ring": int(
                    (ring_hit & ~parent.known_mask.ravel()).sum()
                ),
                "inert_parent_cells_intersecting_ring": int(
                    (ring_hit & ~parent.burnable_mask.ravel()).sum()
                ),
                "remaining_potential_node_positive_ring_overlap_count": int((leakage > 1e-8).sum()),
                "remaining_potential_node_ring_overlap_geometry_m2": float(leakage.sum()),
                "protected_original_roof_node_cleared_count": int(
                    sum(
                        roof_cleared[n]
                        for n, ids in enumerate(base.owners)
                        if protected.intersection(ids)
                    )
                ),
                "core_and_actual_source_geometries_inside_inner": bool(
                    anchors.difference(inner).area < 1e-8
                ),
                "inner_remaining_potential_covered_capacity_m2": float(
                    base.node_inputs.parameters.covered_area_m2.ravel()[remaining][
                        shapely.intersects(remaining_shapes, inner)
                    ].sum()
                ),
                "capacity_is_not_predicted_loss": True,
                "conservative_cut_evidence": cut,
            }
        )
        for name, geometry in (
            ("inner_protected_union_buffer", inner),
            ("nominal_ring", ring),
            ("cleared_roof_geometry", roof_geometry),
            ("whole_cell_grass_action", grass_geometry),
        ):
            features.append(
                {
                    "type": "Feature",
                    "properties": {"plan_id": plan_id, "layer": name},
                    "geometry": mapping(geometry),
                }
            )
    return plans, features, mapping(hull)
