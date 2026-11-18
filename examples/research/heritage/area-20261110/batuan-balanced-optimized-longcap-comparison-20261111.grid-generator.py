"""Search a preregistered 51-point geometric ring grid without CA or graphs."""

from __future__ import annotations

import argparse
import time
from copy import deepcopy
from pathlib import Path

from prepare_heritage_balanced_roof_bands import finite_retained_sources, generate_balanced_bands
from prepare_heritage_joint_longcap import encoded, immutable
from run_heritage_joint_comparison import finite_source_catalog_certificate, plan_from_mapping

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]
OFFSETS = tuple(i * 2.5 for i in range(17))
WIDTHS = (15.01, 17.5, 20.0)
TOLERANCE_M2 = 1e-6


def rejection_reasons(plan, expected_sources, actual_sources):
    proof = plan["conservative_cut_evidence"]
    conditions = {
        "potential_cross_partition_edge": proof[
            "potential_unordered_inner_outer_pairs_within_radiation_support"
        ]
        != 0,
        "distance_not_strictly_above_support": proof[
            "minimum_inner_outer_physical_polygon_distance_m"
        ]
        is None
        or proof["minimum_inner_outer_physical_polygon_distance_m"]
        <= proof["radiation_support_radius_m"] + 1e-9,
        "no_conservative_cut": proof["certified_no_potential_radiation_or_contact_edge"]
        is not True,
        "protected_roof_cleared": plan["protected_original_roof_node_cleared_count"] != 0,
        "protected_geometry_not_inside": plan["core_and_actual_source_geometries_inside_inner"]
        is not True,
        "ring_outside_domain": plan["nominal_ring_outside_parent_rectangle_area_m2"] > 1e-8,
        "unknown_ring_cell": plan["unknown_parent_cells_intersecting_ring"] != 0,
        "retained_potential_ring_fuel": plan["remaining_potential_node_positive_ring_overlap_count"]
        != 0,
        "source_changed": actual_sources != expected_sources,
    }
    return [reason for reason, failed in conditions.items() if failed]


def objectives(plan):
    return plan["C_construction_action_area_m2"], plan[
        "inner_remaining_potential_covered_capacity_m2"
    ]


def geometry_archive(plans, *, tolerance=TOLERANCE_M2):
    """Deduplicate exact physical actions, retaining the tightest capacity witness."""
    groups = {}
    for plan in plans:
        if not plan["geometry_certified"]:
            continue
        physical = content_hash([plan["demolished_ids"], plan["cleared_physical_cell_ids"]])
        groups.setdefault(physical, []).append(plan)
    canonical = []
    for key, variants in groups.items():
        best = min(
            variants,
            key=lambda p: (
                objectives(p)[1],
                objectives(p)[0],
                p["inner_hull_buffer_m"],
                p["nominal_ring_width_m"],
            ),
        )
        row = deepcopy(best)
        row["physical_action_sha256"] = key
        row["equivalent_parameter_plan_ids"] = sorted(p["plan_id"] for p in variants)
        canonical.append(row)
    canonical.sort(key=lambda p: (*objectives(p), p["plan_id"]))

    def dominates(a, b):
        left, right = objectives(a), objectives(b)
        return all(x <= y + tolerance for x, y in zip(left, right, strict=True)) and any(
            x < y - tolerance for x, y in zip(left, right, strict=True)
        )

    frontier = [p for p in canonical if not any(dominates(other, p) for other in canonical)]
    if not frontier:
        return canonical, [], []
    cheapest = min(frontier, key=lambda p: (*objectives(p), p["plan_id"]))
    tightest = min(frontier, key=lambda p: (objectives(p)[1], objectives(p)[0], p["plan_id"]))
    selected = [cheapest]
    if tightest["physical_action_sha256"] != cheapest["physical_action_sha256"]:
        selected.append(tightest)
    remaining = [
        p
        for p in frontier
        if p["physical_action_sha256"] not in {x["physical_action_sha256"] for x in selected}
    ]
    if remaining:
        remaining.sort(key=lambda p: (*objectives(p), p["plan_id"]))
        selected.append(remaining[len(remaining) // 2])
    return canonical, frontier, selected


def prepare(reference_catalog_path, output, top_catalog_path, design_path):
    started = time.monotonic()
    old = read_json(reference_catalog_path)
    config_path = Path(old["reference_config_path"])
    config = read_json(config_path)
    reference_path = Path(old["reference_master_experiment_path"])
    reference = read_json(reference_path)
    initial = {
        str(reference_catalog_path): file_sha256(reference_catalog_path),
        str(config_path): file_sha256(config_path),
        str(reference_path): file_sha256(reference_path),
    }
    if (
        initial[str(config_path)] != old["reference_config_sha256"]
        or initial[str(reference_path)] != old["reference_master_experiment_sha256"]
    ):
        raise ValueError("original balanced geometry reference changed")
    if content_hash(reference["problem"]) != reference["problem_sha256"]:
        raise ValueError("reference problem metadata hash differs")
    for path, digest in reference["references_sha256"].items():
        if file_sha256(path) != digest:
            raise ValueError("frozen input changed")
    for relative, digest in old["support_operator_source_sha256"].items():
        if file_sha256(ROOT / relative) != digest:
            raise ValueError("operator support implementation changed")
    seed = tuple(old["original_seed_ids"])
    actual = finite_retained_sources(reference["problem"]["fires"], seed)
    design = {
        "schema_version": "heritage-balanced-geometric-grid-design-1",
        "registered_before_parameter_calculation": True,
        "new_CA_checks": 0,
        "outcome_files_read_for_selection": [],
        "inner_offsets_m": list(OFFSETS),
        "ring_widths_m": list(WIDTHS),
        "combination_count": 51,
        "inner_rule": "convex_hull(full_core_and_actual_source_roof_geometries).buffer(r)",
        "action_rule": (
            "original_seed_plus_all_ordinary_owners_of_positive_area_roof_ring_patches;"
            "whole_eligible5m_grass_cells"
        ),
        "filters": [
            "strict_support_cut",
            "core_and_source_retained",
            "no_domain_clip",
            "no_unknown_or_retained_potential_ring_fuel",
            "same_first_retained_sources",
        ],
        "objectives": [
            "construction_geometry_union_m2",
            "inner_remaining_physical_covered_capacity_proxy_m2",
        ],
        "dominance_absolute_tolerance_m2": TOLERANCE_M2,
        "physical_action_deduplication": (
            "identical_roofIDs_and_grass_cellIDs;tightest_capacity_witness"
        ),
        "top_catalog_rule": (
            "min_C_then_min_capacity_then_median_C_remaining_non_dominated;at_most3_distinct_actions"
        ),
        "soft_resource_rule": (
            "if_first17_at15.01m_exceed240s_return_partial_grid_and_do_not_claim51point_optimum"
        ),
        "original_artifact_sha256": initial,
        "reference_master_problem_sha256": reference["problem_sha256"],
        "generator_sha256": file_sha256(Path(__file__)),
        "geometry_helper_sha256": file_sha256(
            ROOT / "scripts/prepare_heritage_balanced_roof_bands.py"
        ),
    }
    immutable(design_path, encoded(design))
    bundle, transport, inventory = load_study(Path(config["study_path"]))
    base = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=config["max_nodes"],
        core_overlap_policy=config.get("core_overlap_policy", "reject"),
    )
    plans, timings = [], []
    for width in WIDTHS:
        batch_started = time.monotonic()
        batch, _, _ = generate_balanced_bands(
            bundle.request,
            base,
            inventory,
            seed_ids=seed,
            actual_source_ids=actual.values(),
            radius_m=transport.exposure_radius_m,
            offsets=OFFSETS,
            width_m=width,
        )
        for plan in batch:
            sources = finite_retained_sources(reference["problem"]["fires"], plan["demolished_ids"])
            reasons = rejection_reasons(plan, actual, sources)
            plan.update(
                geometry_certified=not reasons,
                geometry_rejection_reasons=reasons,
                finite_catalog_actual_sources=sources,
            )
        plans.extend(batch)
        timings.append(
            {
                "ring_width_m": width,
                "combinations": len(batch),
                "elapsed_s": time.monotonic() - batch_started,
            }
        )
        print(
            {
                "width_m": width,
                "evaluated": len(plans),
                "certified": sum(p["geometry_certified"] for p in plans),
            },
            flush=True,
        )
        if width == WIDTHS[0] and time.monotonic() - started > 240:
            break
    canonical, frontier, top = geometry_archive(plans)
    complete = len(plans) == 51
    minimum = min((objectives(p)[0] for p in canonical), default=None)
    minimizers = (
        [p for p in canonical if abs(objectives(p)[0] - minimum) <= TOLERANCE_M2]
        if canonical
        else []
    )
    baseline = next(p for p in old["plans"] if p["inner_hull_buffer_m"] == 10)
    certificate = (
        finite_source_catalog_certificate(
            [plan_from_mapping(p) for p in top],
            reference["problem"]["fires"],
            inventory_sha256=inventory.sha256,
            reference_problem_sha256=reference["problem_sha256"],
        )
        if top
        else None
    )
    top_report = {
        "schema_version": "heritage-balanced-geometry-grid-top-catalog-1",
        "village_id": "batuan",
        "new_CA_checks": 0,
        "new_independent_fire_draws": 0,
        "outcome_files_read_for_selection": [],
        "selection_scope": "at_most3_geometric_C_capacity_proxy_tradeoffs;not_CA_loss_frontier",
        "reference_geometry_report_path": str(output),
        "pre_registered_design_path": str(design_path),
        "pre_registered_design_sha256": file_sha256(design_path),
        "reference_config_path": str(config_path),
        "reference_master_experiment_path": str(reference_path),
        "reference_master_problem_sha256": reference["problem_sha256"],
        "original_seed_ids": list(seed),
        "core_ids": old["core_ids"],
        "actual_source_roof_ids": old["actual_source_roof_ids"],
        "source_area_limit_m2_reference_only": config["source_area_limit_m2"],
        "source_area_tolerance_m2_reference_only": config["source_area_tolerance_m2"],
        "old_area_domain_not_extended": True,
        "production_requires_finite_declared_plans_opt_in": True,
        "plans": top,
        "finite_source_catalog_certificate": certificate,
    }
    write_json(top_catalog_path, top_report)
    report = {
        "schema_version": "heritage-balanced-geometric-grid-audit-1",
        "village_id": "batuan",
        "new_CA_checks": 0,
        "radiation_or_contact_graphs_built": 0,
        "new_independent_fire_draws": 0,
        "outcome_files_read_for_selection": [],
        "pre_registered_design_path": str(design_path),
        "pre_registered_design_sha256": file_sha256(design_path),
        "parameter_combinations_declared": 51,
        "parameter_combinations_evaluated": len(plans),
        "parameter_grid_complete": complete,
        "certified_parameter_combinations": sum(p["geometry_certified"] for p in plans),
        "distinct_certified_physical_actions": len(canonical),
        "geometry_non_dominated_plan_ids": [p["plan_id"] for p in frontier],
        "geometry_non_dominated_archive": frontier,
        "minimum_C_grid_certificate": {
            "scope": "only_certified_actions_in_this_pre_registered_parameter_grid",
            "all51_combinations_evaluated": complete,
            "minimum_C_m2": minimum,
            "minimizer_plan_ids": [p["plan_id"] for p in minimizers],
            "all_certified_actions_C_at_least_minimum_within_tolerance": all(
                objectives(p)[0] >= minimum - TOLERANCE_M2 for p in canonical
            )
            if canonical
            else False,
            "absolute_numeric_tolerance_m2": TOLERANCE_M2,
            "unrestricted_geometry_or_CA_optimality_proven": False,
        },
        "fixed_b10_w20_reference_C_m2": baseline["C_construction_action_area_m2"],
        "minimum_C_reduction_vs_fixed_b10_w20_m2": baseline["C_construction_action_area_m2"]
        - minimum
        if minimum is not None
        else None,
        "capacity_proxy_interpretation": (
            "sum_unique_remaining_covered_node_areas_intersecting_inner;"
            "not_observed_or_predicted_burned_area_or_building_count"
        ),
        "top_catalog_path": str(top_catalog_path),
        "top_catalog_sha256": file_sha256(top_catalog_path),
        "selected_top_plan_ids": [p["plan_id"] for p in top],
        "all_parameter_rows": plans,
        "batch_timings": timings,
        "reference_geometry_catalog_path": str(reference_catalog_path),
        "original_artifact_sha256": initial,
        "old_three_arm_catalog_modified": False,
        "elapsed_s": time.monotonic() - started,
        "generator_sha256": file_sha256(Path(__file__)),
    }
    if any(file_sha256(path) != digest for path, digest in initial.items()):
        raise ValueError("an original frozen artifact changed during the geometric search")
    write_json(output, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--reference-catalog",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-bands-20261111.json",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-geometry-grid-20261111.json",
    )
    parser.add_argument(
        "--top-catalog",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-geometry-top-20261111.json",
    )
    parser.add_argument(
        "--design",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-geometry-design-20261111.json",
    )
    args = parser.parse_args()
    report = prepare(
        args.reference_catalog.resolve(),
        args.output.resolve(),
        args.top_catalog.resolve(),
        args.design.resolve(),
    )
    print(
        {
            "new_CA_checks": 0,
            "complete": report["parameter_grid_complete"],
            "elapsed_s": report["elapsed_s"],
            "minimum": report["minimum_C_grid_certificate"],
            "top": report["selected_top_plan_ids"],
        }
    )


if __name__ == "__main__":
    main()
