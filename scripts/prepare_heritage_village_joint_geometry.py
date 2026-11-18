"""Prepare graph-free village long-fire and 51-point finite geometric catalogs.

Uses existing geometry helpers without modifying any previously bound producer.
No CA result is read for selecting a ring or predicting protection/fire loss.
"""

from __future__ import annotations

import argparse
import time
from copy import deepcopy
from pathlib import Path

from optimize_heritage_balanced_geometry import (
    OFFSETS,
    WIDTHS,
    geometry_archive,
    objectives,
    rejection_reasons,
)
from prepare_heritage_balanced_roof_bands import finite_retained_sources, generate_balanced_bands
from prepare_heritage_joint_longcap import encoded, immutable
from prepare_heritage_optimized_balanced_catalog import zero_ca_validation
from run_heritage_joint_comparison import (
    finite_source_catalog_certificate,
    plan_from_mapping,
    read_master_reference,
    validate_config,
)

from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]


def choose_two(plans):
    """Select grid-minimum C and the predefined b10/w20, without fire outcomes."""
    if len(plans) != 51:
        raise ValueError("all 51 preregistered parameter combinations are required")
    canonical, frontier, _ = geometry_archive(plans)
    if not canonical:
        raise ValueError("no geometrically certified candidate;no protection claim is available")
    minimum = min(canonical, key=lambda p: (*objectives(p), p["plan_id"]))
    control = [
        p for p in plans if p["inner_hull_buffer_m"] == 10 and p["nominal_ring_width_m"] == 20
    ]
    if len(control) != 1 or not control[0]["geometry_certified"]:
        raise ValueError("predefined b10/w20 control lacks the registered geometry certificate")
    control = deepcopy(control[0])
    if control["plan_id"] == minimum["plan_id"]:
        control["plan_id"] += "-control"
    return [deepcopy(minimum), control], canonical, frontier


def immutable_mapping(path, value):
    immutable(path, encoded(value))
    return file_sha256(path)


def prepare(template_path, output_config, output_geometry_config, report_prefix):
    start = time.monotonic()
    template_path, output_config, output_geometry_config, report_prefix = [
        Path(p).resolve()
        for p in (template_path, output_config, output_geometry_config, report_prefix)
    ]
    template = read_json(template_path)
    validate_config(template)
    if (
        template.get("terminal_fire_verification") is not True
        or template.get("terminal_horizon_cap_s") != 172800
    ):
        raise ValueError("explicit 172800s whole-fire factory template is required")
    if len(template["plans"]) != 5 or template["max_new_checks"] != 40:
        raise ValueError("the original five-arm/eight-scenario template is required")
    reference, _ = read_master_reference(template_path, template)
    seed = next(p["demolished_ids"] for p in template["plans"] if p["plan_id"] == "roof-seed")
    village = template["village_id"]
    stem = str(report_prefix)
    paths = {
        "design": Path(stem + "-design.json"),
        "grid": Path(stem + "-grid.json"),
        "catalog": Path(stem + "-catalog.json"),
        "geojson": Path(stem + ".geojson"),
        "source_certificate": output_geometry_config.with_suffix(".finite-source-certificate.json"),
        "receipt": Path(stem + "-preparation.json"),
    }
    input_paths = [
        template_path,
        Path(template["roof_seed_receipt_path"]),
        Path(template["roof_seed_search_snapshot_path"]),
        Path(template["vegetation_proposals_path"]),
        Path(template["reference_master_experiment_path"]),
        Path(template["reference_master_config_path"]),
        Path(__file__).resolve(),
        *[
            ROOT / "scripts" / name
            for name in (
                "optimize_heritage_balanced_geometry.py",
                "prepare_heritage_balanced_roof_bands.py",
                "prepare_heritage_all_roof_bands.py",
                "prepare_heritage_optimized_balanced_catalog.py",
                "prepare_heritage_joint_area_campaign.py",
            )
        ],
    ]
    initial = {str(p): file_sha256(p) for p in input_paths}
    actual = finite_retained_sources(reference["problem"]["fires"], seed)
    design = {
        "schema_version": "heritage-village-balanced-grid-design-1",
        "village_id": village,
        "registered_before_parameter_calculation": True,
        "inner_offsets_m": list(OFFSETS),
        "ring_widths_m": list(WIDTHS),
        "parameter_combinations": 51,
        "control_inner_offset_m": 10,
        "control_ring_width_m": 20,
        "selection": "minimum_C_among_geometry_certified_grid_actions;fixed_b10w20_control",
        "hull_anchors": "full_core_and_actual_source_roof_geometries",
        "original_seed_ids": seed,
        "first_retained_sources": actual,
        "construction_ground_cost_m2_per_cell": 25,
        "capacity_proxy_is_not_observed_or_predicted_fire_loss": True,
        "new_CA_checks": 0,
        "outcome_files_read_for_ring_selection": [],
        "original_inputs_sha256": initial,
    }
    immutable_mapping(paths["design"], design)
    long_config = deepcopy(template)
    long_config.update(
        max_wall_time_s=172800.0,
        additional_frozen_inputs_sha256=initial,
        scope="five_finite_joint_candidates;full_fire_172800s_cap;old_roof_C_anchor_not_transferred",
    )
    validate_config(long_config)
    immutable_mapping(output_config, long_config)
    bundle, transport, inventory = load_study(Path(template["study_path"]))
    base = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=template["max_nodes"],
        core_overlap_policy=template.get("core_overlap_policy", "reject"),
    )
    all_plans, features, timings = [], [], []
    for width in WIDTHS:
        batch_start = time.monotonic()
        batch, geojson, _ = generate_balanced_bands(
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
        all_plans.extend(batch)
        features.extend(geojson)
        timings.append({"width_m": width, "elapsed_s": time.monotonic() - batch_start})
        print({"width_m": width, "parameter_rows": len(all_plans)}, flush=True)
    selected, canonical, frontier = choose_two(all_plans)
    selection = {
        "schema_version": "heritage-village-balanced-two-arm-catalog-1",
        "village_id": village,
        "plans": selected,
        "reference_master_problem_sha256": reference["problem_sha256"],
        "design_sha256": file_sha256(paths["design"]),
        "selection_reads_no_new_CA_outcomes": True,
        "geometry_does_not_predict_core_safety_or_fire_losses": True,
    }
    grid = {
        "schema_version": "heritage-village-balanced-grid-results-1",
        "village_id": village,
        "parameter_grid_complete": len(all_plans) == 51,
        "all_parameter_rows": all_plans,
        "distinct_certified_actions": len(canonical),
        "geometry_non_dominated_plan_ids": [p["plan_id"] for p in frontier],
        "selected_minimum_C_plan_id": selected[0]["plan_id"],
        "minimum_certified_grid_C_m2": selected[0]["C_construction_action_area_m2"],
        "minimum_C_scope": "only_the_declared_51_point_certified_geometry_grid;not_CA_optimality",
        "new_CA_checks": 0,
        "radiation_or_contact_graphs_built": 0,
        "batch_timings": timings,
        "geometry_C_m2_by_selected_plan": {
            p["plan_id"]: p["C_construction_action_area_m2"] for p in selected
        },
    }
    immutable_mapping(paths["grid"], grid)
    immutable_mapping(paths["catalog"], selection)
    selected_ids = {p["plan_id"].removesuffix("-control") for p in selected}
    immutable_mapping(
        paths["geojson"],
        {
            "type": "FeatureCollection",
            "features": [f for f in features if f["properties"]["plan_id"] in selected_ids],
        },
    )
    rows = [
        {
            "plan_id": p["plan_id"],
            "demolished_ids": p["demolished_ids"],
            "cleared_physical_cell_ids": p["cleared_physical_cell_ids"],
            "ground_evidence": "predeclared_geometry_grid_or_b10w20;not_field_surveyed",
        }
        for p in selected
    ]
    certificate = finite_source_catalog_certificate(
        [plan_from_mapping(p) for p in rows],
        reference["problem"]["fires"],
        inventory_sha256=inventory.sha256,
        reference_problem_sha256=reference["problem_sha256"],
    )
    if any(row["actual_source_id"] != actual[row["fire_id"]] for row in certificate["rows"]):
        raise ValueError("finite geometry catalog changes an original actual source")
    immutable_mapping(paths["source_certificate"], certificate)
    additional = {
        **initial,
        **{str(p): file_sha256(p) for k, p in paths.items() if k != "receipt"},
    }
    geometry_config = deepcopy(long_config)
    geometry_config.update(
        plans=rows,
        source_coverage_policy="finite_declared_plans",
        max_new_checks=16,
        max_total_checks=16,
        vegetation_proposals_path=str(paths["catalog"]),
        vegetation_proposals_sha256=file_sha256(paths["catalog"]),
        additional_frozen_inputs_sha256=additional,
        scope="two_finite_geometry_catalog_arms;old_area_cap_mesh_reference_only;new_CA_required",
        seed_reverification="original_seed_receipt_is_provenance_only;all16_checks_are_new",
        comparison_arm_roles={
            rows[0]["plan_id"]: "geometric_grid_minimum_C",
            rows[1]["plan_id"]: "predefined_b10w20_control",
        },
    )
    geometry_config.pop("roof_replacement_rule", None)
    validate_config(geometry_config)
    immutable_mapping(output_geometry_config, geometry_config)
    audit = zero_ca_validation(output_geometry_config, geometry_config, certificate)
    if any(
        abs(r["prescribed_source_footprint_area_m2"] - 25) > 1e-8
        for r in audit["plan_source_grass_checks"]
    ):
        raise ValueError("finite source footprint no longer has its full 25m2 budget")
    preflight_path = Path(template["reference_master_experiment_path"]).with_name("preflight.json")
    resource = read_json(preflight_path)
    receipt = {
        "schema_version": "heritage-village-longfire-geometry-preparation-1",
        "village_id": village,
        "five_arm_config": str(output_config),
        "five_arm_config_sha256": file_sha256(output_config),
        "two_arm_config": str(output_geometry_config),
        "two_arm_config_sha256": file_sha256(output_geometry_config),
        "planned_five_arm_checks": 40,
        "planned_two_arm_checks": 16,
        "terminal_horizon_cap_s": 172800,
        "maximum_wall_time_s_per_job": 172800,
        "original_seed_C_m2": read_json(template["roof_seed_receipt_path"])[
            "verified_feasible_row"
        ]["cleared_area_m2"],
        "original_seed_eight_native_checks_verified_by_factory": True,
        "native_3600s_protection_is_not_terminal_feasibility": True,
        "geometry_selection": grid,
        "source_and_parent_eligibility_audit": audit,
        "capacity_upper_bound_reference_path": str(preflight_path),
        "capacity_upper_bound_reference_sha256": file_sha256(preflight_path),
        "capacity_upper_bounds": resource,
        "capacity_bound_scope": (
            "identical_full_prepared_mesh;actions_only_disable_nodes;frozen_integration_membership"
        ),
        "original_inputs_sha256": initial,
        "new_CA_checks": 0,
        "new_radiation_or_contact_graphs": 0,
        "old_B_generators_or_artifacts_modified": False,
        "producer_frozen": False,
        "elapsed_s": time.monotonic() - start,
    }
    if any(file_sha256(path) != digest for path, digest in initial.items()):
        raise ValueError("an original input or helper changed during preparation")
    immutable_mapping(paths["receipt"], receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--long-config", type=Path, required=True)
    parser.add_argument("--geometry-config", type=Path, required=True)
    parser.add_argument("--report-prefix", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.template, args.long_config, args.geometry_config, args.report_prefix)
    print(
        {k: result[k] for k in ("five_arm_config", "two_arm_config", "elapsed_s", "new_CA_checks")}
    )


if __name__ == "__main__":
    main()
