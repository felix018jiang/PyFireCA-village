"""Generate geometry-only whole-parent vegetation rings around all inventory roofs.

No solver, radiation graph, result trajectory, or experimental outcome is read.
The conservative edge check uses physical polygon distances, which enclose all
point and area-quadrature radiation supports and boundary-contact supports.
"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import numpy as np
import shapely
from shapely.geometry import box, mapping, shape
from shapely.strtree import STRtree

from pyfireca.planning.area_candidates import build_cleared_area_function
from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine, RoofJointPlan
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def parent_cell_polygons(grid):
    cells = np.arange(grid.width * grid.height)
    row, col = np.divmod(cells, grid.width)
    x = grid.x_min_m + col * grid.cell_size_m
    y = grid.y_max_m - row * grid.cell_size_m
    return shapely.box(x, y - grid.cell_size_m, x + grid.cell_size_m, y)


def conservative_partition_check(base, cleared_cells, inner, radius_m):
    """Overestimate every possible directed radiation/contact edge, without CA.

    Roof refinement and quadrature points remain inside these original polygons.
    Every existing radiation edge has unshifted support distance <= radius_m;
    wind tilt changes its kernel but does not extend this candidate radius.
    Contact requires positive shared polygon boundary, hence distance zero.
    We include the union of all burnable/source/target masks, even if a node
    could not actually burn. This establishes only a finite-model operator cut.
    """
    inputs = base.node_inputs
    potential = np.logical_or.reduce(
        [
            np.asarray(getattr(inputs, name)).ravel()
            for name in ("burnable_mask", "source_mask", "target_mask")
        ]
    )
    potential &= ~np.isin(base.cell_id, cleared_cells)
    nodes = np.flatnonzero(potential)
    polygons = np.asarray(base.geometry, dtype=object)[nodes]
    inside = shapely.intersects(polygons, inner)
    inner_polygons, outer_polygons = polygons[inside], polygons[~inside]
    minimum = None
    pairs = 0
    if len(inner_polygons) and len(outer_polygons):
        tree = STRtree(outer_polygons)
        pairs = int(tree.query(inner_polygons, predicate="dwithin", distance=radius_m).shape[1])
        _, distances = tree.query_nearest(inner_polygons, all_matches=False, return_distance=True)
        minimum = float(np.min(distances))
    separated = pairs == 0 and (minimum is None or minimum > radius_m + 1e-9)
    return {
        "schema_version": "physical-support-conservative-cross-partition-cut-1",
        "potential_inner_node_count": int(inside.sum()),
        "potential_outer_node_count": int((~inside).sum()),
        "potential_node_mask": "burnable_or_source_or_target;roof_seed_removal_not_assumed",
        "minimum_inner_outer_physical_polygon_distance_m": minimum,
        "radiation_support_radius_m": float(radius_m),
        "potential_unordered_inner_outer_pairs_within_radiation_support": pairs,
        "certified_no_potential_radiation_or_contact_edge": separated,
        "proof_scope": "finite_frozen_domain_and_current_operator_supports;not_field_fire_safety",
        "roof_refinement_preserves_certificate": True,
        "basis": (
            "all_refined_roof_and_quadrature_supports_subset_original_polygons;"
            "contact_requires_boundary_intersection"
        ),
    }


def generate_bands(parent, base, inventory, *, seed_ids, radius_m, offsets=(0, 10, 20), width_m=20):
    if parent.grid.cell_size_m != 5:
        raise ValueError("whole-parent band proposals require the original 5 m grid")
    if width_m <= 0 or any(r < 0 for r in offsets):
        raise ValueError("ring widths must be positive and offsets nonnegative")
    roof_union = shapely.union_all([b.geometry for b in inventory.buildings])
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
    # Inventory-vector overlap is checked independently of the parent raster.
    shapely.prepare(roof_union)
    possible_overlap = cells[eligible][shapely.intersects(cells[eligible], roof_union)]
    vector_overlap = shapely.area(shapely.intersection(possible_overlap, roof_union))
    if np.any(vector_overlap > 1e-8):
        raise ValueError("an eligible nonroof parent footprint overlaps actual roof geometry")
    area_function = build_cleared_area_function(
        base.owners,
        [g.area for g in base.geometry],
        area_basis="original_disjoint_roof_geometry;all_owners_removed",
    )
    roof_area = float(area_function(seed_ids))
    roof_clear = np.asarray([bool(ids) and set(ids) <= set(seed_ids) for ids in base.owners])
    roof_covered = float(base.node_inputs.parameters.covered_area_m2.ravel()[roof_clear].sum())
    domain = box(
        parent.grid.x_min_m,
        parent.grid.y_max_m - parent.grid.height * 5,
        parent.grid.x_min_m + parent.grid.width * 5,
        parent.grid.y_max_m,
    )
    plans, features = [], []
    for offset in offsets:
        inner = roof_union if offset == 0 else roof_union.buffer(offset, quad_segs=64)
        outer = roof_union.buffer(offset + width_m, quad_segs=64)
        ring = outer.difference(inner)
        shapely.prepare(ring)
        hit = shapely.intersects(cells, ring)
        selected = np.flatnonzero(hit & eligible)
        selected_polygons = cells[selected]
        action_union = shapely.union_all(selected_polygons)
        construction = len(selected) * parent.grid.cell_area_m2
        if not np.isclose(action_union.area, construction, rtol=0, atol=1e-6):
            raise ValueError(
                "distinct complete parent footprints did not have additive action area"
            )
        if action_union.intersection(roof_union).area > 1e-8:
            raise ValueError("vegetation action construction area overlaps roof inventory")
        potential = np.logical_or.reduce(
            [
                np.asarray(getattr(base.node_inputs, name)).ravel()
                for name in ("burnable_mask", "source_mask", "target_mask")
            ]
        )
        remaining = potential & ~np.isin(base.cell_id, selected)
        remaining_polygons = np.asarray(base.geometry, dtype=object)[remaining]
        fuel_in_band = shapely.area(
            shapely.intersection(
                remaining_polygons[shapely.intersects(remaining_polygons, ring)], ring
            )
        )
        proof = conservative_partition_check(base, selected, inner, radius_m)
        exclusions = {}
        for label, mask in {
            "outside_domain": ~parent.domain_mask.ravel(),
            "unknown": ~parent.known_mask.ravel(),
            "nonburnable": ~parent.burnable_mask.ravel(),
            "roof_raster_cell": np.isin(np.arange(len(cells)), roof_cells),
            "zero_covered_fuel": parent.parameters.covered_area_m2.ravel() <= 0,
        }.items():
            ids = np.flatnonzero(hit & mask)
            exclusions[label] = {
                "parent_cell_count": len(ids),
                "nominal_ring_overlap_geometry_m2": float(
                    shapely.area(shapely.intersection(cells[ids], ring)).sum()
                ),
                "physical_cell_ids": ids.tolist(),
            }
        plan_id = f"roof-seed-allroof-ring-r{offset:g}-w{width_m:g}"
        plans.append(
            {
                "plan_id": plan_id,
                "demolished_ids": list(seed_ids),
                "cleared_physical_cell_ids": selected.tolist(),
                "ring_offset_m": offset,
                "nominal_ring_width_m": width_m,
                "buffer_quad_segs": 64,
                "cell_selection": (
                    "any_footprint_intersection_with_ring;full_5m_cell;boundary_touches_included"
                ),
                "cleared_vegetation_parent_cell_count": len(selected),
                "cleared_vegetation_ground_footprint_area_m2": construction,
                "cleared_vegetation_covered_fuel_area_m2": float(
                    parent.parameters.covered_area_m2.ravel()[selected].sum()
                ),
                "cleared_roof_geometry_union_area_m2": roof_area,
                "cleared_roof_covered_area_m2": roof_covered,
                "C_construction_action_area_m2": roof_area + construction,
                "nominal_ring_geometry_area_m2": float(ring.area),
                "nominal_ring_outside_parent_rectangle_area_m2": float(
                    ring.difference(domain).area
                ),
                "nominal_ring_not_in_action_footprints_area_m2": float(
                    ring.difference(action_union).area
                ),
                "excluded_cell_categories_nonexclusive": exclusions,
                "remaining_potential_node_positive_ring_overlap_count": int(
                    (fuel_in_band > 1e-8).sum()
                ),
                "remaining_potential_node_ring_overlap_geometry_m2": float(fuel_in_band.sum()),
                "conservative_cut_evidence": proof,
                "source_core_partition": {
                    "all_original_inventory_roofs_inside_inner": bool(
                        roof_union.difference(inner).area <= 1e-8
                    ),
                    "core_ids_inside_inner": [
                        t.building_id
                        for t in inventory.targets
                        if inventory.by_id[t.building_id].geometry.difference(inner).area <= 1e-8
                    ],
                    "all_inventory_source_roof_geometries_inside_inner": True,
                    "cleared_cells_intersect_any_roof_parent_cell": bool(
                        np.isin(selected, roof_cells).any()
                    ),
                    "actual_source_footprints_are_checked_from_frozen_master_separately": True,
                },
                "physical_eligibility_and_unchanged_roof_masks_verified": True,
                "scope": (
                    "geometry_proposal_and_finite_operator_cut_only;"
                    "heritage_core_feasibility_and_final_losses_require_CA"
                ),
            }
        )
        features.extend(
            [
                {
                    "type": "Feature",
                    "properties": {"plan_id": plan_id, "layer": "nominal_ring"},
                    "geometry": mapping(ring),
                },
                {
                    "type": "Feature",
                    "properties": {
                        "plan_id": plan_id,
                        "layer": "whole_cell_action_footprints",
                        "construction_area_m2": construction,
                    },
                    "geometry": mapping(action_union),
                },
            ]
        )
    return (
        plans,
        features,
        {
            "inventory_roof_union_area_m2": float(roof_union.area),
            "all_inventory_object_count": len(inventory.buildings),
            "eligible_parent_cell_count": int(eligible.sum()),
            "physical_parent_cell_count": len(cells),
            "all_roof_parent_cell_count": len(roof_cells),
        },
    )


def prepare(config_path, output, geojson_output):
    started = time.monotonic()
    config = read_json(config_path)
    reference_path = Path(config["reference_master_experiment_path"])
    reference = read_json(reference_path)
    if file_sha256(reference_path) != config["reference_master_experiment_sha256"]:
        raise ValueError("frozen reference master changed")
    for path, digest in reference["references_sha256"].items():
        if file_sha256(path) != digest:
            raise ValueError("frozen physical input changed")
    study_path = (config_path.parent / config["study_path"]).resolve()
    bundle, transport, inventory = load_study(study_path)
    seed = ("B0061", "B0071", "B0076")
    if next(p["demolished_ids"] for p in config["plans"] if p["plan_id"] == "roof-seed") != list(
        seed
    ):
        raise ValueError("the original three-roof seed must remain fixed")
    if transport.exposure_radius_m != 15:
        raise ValueError("the declared ring design requires the frozen 15m radiation support")
    base = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
    )
    plans, features, geometry = generate_bands(
        bundle.request, base, inventory, seed_ids=seed, radius_m=transport.exposure_radius_m
    )
    sources = {}
    checker = object.__new__(RoofJointEvaluationEngine)
    checker.bundle, checker.layout = bundle, base
    legal_checks = 0
    for label, fire in reference["problem"]["fires"].items():
        draw = fire["protocol"]["draws"][0]
        actual = next(identifier for identifier in draw["priority_ids"] if identifier not in seed)
        footprint = shape(fire["footprints"][actual]["geometry"])
        point = footprint.representative_point()
        scenario = EvaluationScenario(label, (Ignition((point.x, point.y)),), config["horizon_s"])
        for plan in plans:
            action = RoofJointPlan(
                plan["plan_id"],
                seed,
                ground_evidence="geometry_only_all_roof_ring;not_surveyed",
                cleared_physical_cell_ids=tuple(plan["cleared_physical_cell_ids"]),
            )
            nodes = checker._vegetation_nodes(scenario, action)
            if len(nodes) != plan["cleared_vegetation_parent_cell_count"]:
                raise ValueError("joint engine whole-parent eligibility count differs")
            legal_checks += 1
        sources[label] = {
            "baseline_source_id": draw["baseline_source_id"],
            "actual_source_id": actual,
            "source_replaced": actual != draw["baseline_source_id"],
            "footprint_area_m2": float(footprint.area),
            "inside_inventory_roof_union": bool(
                footprint.difference(inventory.by_id[actual].geometry).area <= 1e-8
            ),
        }
    write_json(
        geojson_output,
        {
            "type": "FeatureCollection",
            "coordinate_units": "original_case_metric_xy_m",
            "features": features,
        },
    )
    report = {
        "schema_version": "heritage-all-roof-vegetation-ring-proposals-1",
        "village_id": "batuan",
        "new_CA_checks": 0,
        "outcome_files_read_for_design": [],
        "design_rule": (
            "unary_union(all_inventory_roofs).buffer(r+20).difference(buffer(r));r=0,10,20m"
        ),
        "not_a_claim_of_heritage_safety_or_low_loss": True,
        "reference_joint_config_path": str(config_path),
        "reference_joint_config_sha256": file_sha256(config_path),
        "reference_master_experiment_path": str(reference_path),
        "reference_master_experiment_sha256": file_sha256(reference_path),
        "reference_master_problem_sha256": reference["problem_sha256"],
        "source_policy": config["source_policy"],
        "source_area_limit_m2": config["source_area_limit_m2"],
        "source_area_tolerance_m2": config["source_area_tolerance_m2"],
        "source_prefix_and_refinement_and_numerics_unchanged": True,
        "refinement_ids": config["refinement_ids"],
        "roof_scales_m": config["roof_scales_m"],
        "quadrature_spacing_m": config["quadrature_spacing_m"],
        "max_dt_s": config["max_dt_s"],
        "inventory_sha256": inventory.sha256,
        "geometry": geometry,
        "frozen_actual_sources": sources,
        "core_ids": [t.building_id for t in inventory.targets],
        "joint_engine_geometry_only_eligibility_checks": legal_checks,
        "plans": plans,
        "geojson_path": str(geojson_output),
        "geojson_sha256": file_sha256(geojson_output),
        "source_reference_count": len(reference["references_sha256"]),
        "no_field_claim": (
            "unknown_or_unmodeled_fuels_and_outside_domain_are_not_certified;"
            "roof_raster_cells_have_no_modeled_grass_remainder;"
            "inner_vegetation_and_roofs_can_still_burn"
        ),
        "generator_sha256": file_sha256(Path(__file__)),
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
        default=ROOT / "docs/results/heritage-batuan-all-roof-bands-20261111.json",
    )
    parser.add_argument(
        "--geojson",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-all-roof-bands-20261111.geojson",
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
                    "cells": p["cleared_vegetation_parent_cell_count"],
                    "C_m2": p["C_construction_action_area_m2"],
                    "cut_certificate": p["conservative_cut_evidence"][
                        "certified_no_potential_radiation_or_contact_edge"
                    ],
                }
                for p in result["plans"]
            ],
        }
    )


if __name__ == "__main__":
    main()
