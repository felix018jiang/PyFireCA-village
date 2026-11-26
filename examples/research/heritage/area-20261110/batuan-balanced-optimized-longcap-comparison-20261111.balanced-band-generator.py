"""Geometry-only finite catalog of compact core/source enclosure interventions.

These proposals may exceed the historical roof-area domain. Their source
receipt verifies the actual first-retained source for each finite plan; it is
not an extension of the historical area-bound source-coverage certificate.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import shapely
from prepare_heritage_all_roof_bands import (
    conservative_partition_check,
    parent_cell_polygons,
)
from shapely.geometry import box, mapping, shape

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def finite_retained_sources(fires, demolished_ids):
    removed = set(demolished_ids)
    result = {}
    for label, fire in fires.items():
        priority = fire["protocol"]["draws"][0]["priority_ids"]
        actual = next((identifier for identifier in priority if identifier not in removed), None)
        if actual is None:
            raise ValueError("a finite candidate has no retained ignition source")
        result[label] = actual
    return result


def generate_balanced_bands(
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
    hull = anchors.convex_hull
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
        plan_id = f"roof-seed-core-source-hull-ring-b{offset:g}-w{width_m:g}"
        plans.append(
            {
                "plan_id": plan_id,
                "demolished_ids": list(removed),
                "cleared_physical_cell_ids": grass_ids.tolist(),
                "hull_anchor_ids": sorted(protected),
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
            ("inner_hull_buffer", inner),
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


def prepare(config_path, output, geojson_path):
    started = time.monotonic()
    config = read_json(config_path)
    reference_path = Path(config["reference_master_experiment_path"])
    reference = read_json(reference_path)
    if file_sha256(reference_path) != config["reference_master_experiment_sha256"]:
        raise ValueError("original master experiment changed")
    if content_hash(reference["problem"]) != reference["problem_sha256"]:
        raise ValueError("original master problem fingerprint differs")
    for path, digest in reference["references_sha256"].items():
        if file_sha256(path) != digest:
            raise ValueError("frozen physical input changed")
    operator_paths = [
        "src/pyfireca/planning/roof_thermal.py",
        "src/pyfireca/planning/roof_ignition.py",
        "src/pyfireca/village/transport_v3.py",
    ]
    for name in operator_paths:
        if file_sha256(ROOT / name) != reference["source_sha256"][name]:
            raise ValueError("frozen operator support implementation differs")
    seed = ("B0061", "B0071", "B0076")
    if next(p["demolished_ids"] for p in config["plans"] if p["plan_id"] == "roof-seed") != list(
        seed
    ):
        raise ValueError("original three-roof seed changed")
    fires = reference["problem"]["fires"]
    actual = finite_retained_sources(fires, seed)
    study = (config_path.parent / config["study_path"]).resolve()
    bundle, transport, inventory = load_study(study)
    base = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
    )
    plans, features, hull = generate_balanced_bands(
        bundle.request,
        base,
        inventory,
        seed_ids=seed,
        actual_source_ids=actual.values(),
        radius_m=transport.exposure_radius_m,
    )
    for plan in plans:
        observed = finite_retained_sources(fires, plan["demolished_ids"])
        if observed != actual:
            raise ValueError("compact proposal changes an actual retained source")
        plan["finite_catalog_actual_sources"] = observed
        plan["first_retained_sources_match_original_seed"] = True
        plan["outside_historical_roof_area_cap"] = plan["cleared_roof_geometry_union_area_m2"] > (
            config["source_area_limit_m2"] + config["source_area_tolerance_m2"]
        )
        plan["historical_area_coverage_guard_is_not_applicable"] = plan[
            "outside_historical_roof_area_cap"
        ]
    footprints = {
        label: {
            "actual_source_id": identifier,
            "frozen_footprint": fires[label]["footprints"][identifier],
            "frozen_footprint_sha256": content_hash(fires[label]["footprints"][identifier]),
            "footprint_is_wholly_on_retained_source_roof": bool(
                shape(fires[label]["footprints"][identifier]["geometry"])
                .difference(inventory.by_id[identifier].geometry)
                .area
                <= 1e-8
            ),
        }
        for label, identifier in actual.items()
    }
    write_json(
        geojson_path,
        {
            "type": "FeatureCollection",
            "coordinate_units": "original_case_metric_xy_m",
            "features": features,
        },
    )
    report = {
        "schema_version": "heritage-balanced-core-source-ring-catalog-1",
        "village_id": "batuan",
        "new_CA_checks": 0,
        "new_independent_fire_draws": 0,
        "outcome_files_read_for_design": [],
        "scope": "finite_geometry_catalog_beyond_old_roof_cap;no_fire_feasibility_or_Pareto_claim",
        "original_seed_ids": list(seed),
        "core_ids": [t.building_id for t in inventory.targets],
        "actual_source_roof_ids": sorted(set(actual.values())),
        "reference_config_path": str(config_path),
        "reference_config_sha256": file_sha256(config_path),
        "reference_master_experiment_path": str(reference_path),
        "reference_master_experiment_sha256": file_sha256(reference_path),
        "reference_master_problem_sha256": reference["problem_sha256"],
        "historical_source_area_limit_m2_unchanged": config["source_area_limit_m2"],
        "historical_source_area_tolerance_m2_unchanged": config["source_area_tolerance_m2"],
        "does_not_extend_historical_area_coverage_domain": True,
        "finite_source_receipt": {
            "catalog_plans": [p["plan_id"] for p in plans],
            "actual_sources": actual,
            "source_footprints": footprints,
            "coverage_scope": (
                "only_these_declared_action_sets;full_original_priorities_checked;not_area_domain"
            ),
            "old_source_prefix_footprints_refinement_and_numerics_are_reference_only": True,
            "production_runner_requires_explicit_new_finite_catalog_coverage": True,
        },
        "unchanged_reference_numerics": {
            k: config[k]
            for k in (
                "refinement_ids",
                "roof_scales_m",
                "quadrature_spacing_m",
                "max_dt_s",
                "horizon_s",
            )
        },
        "inventory_sha256": inventory.sha256,
        "anchor_convex_hull_geometry": hull,
        "plans": plans,
        "geojson_path": str(geojson_path),
        "geojson_sha256": file_sha256(geojson_path),
        "support_operator_source_sha256": {
            name: file_sha256(ROOT / name) for name in operator_paths
        },
        "generator_sha256": file_sha256(Path(__file__)),
        "helper_sha256": file_sha256(ROOT / "scripts/prepare_heritage_all_roof_bands.py"),
        "elapsed_s": time.monotonic() - started,
    }
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config",
        type=Path,
        default=ROOT
        / "examples/research/heritage/area-20261110/batuan-joint-terminal-comparison.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-bands-20261111.json",
    )
    parser.add_argument(
        "--geojson",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-bands-20261111.geojson",
    )
    args = parser.parse_args()
    result = prepare(args.config.resolve(), args.output.resolve(), args.geojson.resolve())
    print(
        {
            "new_CA_checks": 0,
            "elapsed_s": result["elapsed_s"],
            "plans": [
                {
                    "plan_id": p["plan_id"],
                    "roof_IDs": len(p["demolished_ids"]),
                    "roof_m2": p["cleared_roof_geometry_union_area_m2"],
                    "grass_m2": p["cleared_vegetation_ground_footprint_area_m2"],
                    "C_m2": p["C_construction_action_area_m2"],
                    "cut": p["conservative_cut_evidence"][
                        "certified_no_potential_radiation_or_contact_edge"
                    ],
                }
                for p in result["plans"]
            ],
        }
    )


if __name__ == "__main__":
    main()
