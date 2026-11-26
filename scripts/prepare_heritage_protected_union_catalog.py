"""Freeze local protected-roof-union bands without a village-wide convex hull.

No isolation certificate is required: distances and cross-pairs are diagnostic.
The completed w15.01 arm is an external reference and is never rescheduled.
"""

from __future__ import annotations

import argparse
import time
from copy import deepcopy
from pathlib import Path

from prepare_heritage_balanced_roof_bands import finite_retained_sources
from prepare_heritage_joint_longcap import encoded, immutable
from prepare_heritage_optimized_balanced_catalog import zero_ca_validation
from prepare_heritage_protected_union_bands import generate_protected_union_bands
from run_heritage_joint_comparison import (
    finite_source_catalog_certificate,
    plan_from_mapping,
    read_master_reference,
    validate_config,
)

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout
from pyfireca.planning.strategy_io import file_sha256, read_json
from pyfireca.planning.study_io import load_study

ROOT = Path(__file__).resolve().parents[1]
WIDTHS = (0.01, 2.5, 5.0, 10.0, 15.01)


def checked_union_rows(plans, fires, expected_sources):
    if tuple(p["nominal_ring_width_m"] for p in plans) != WIDTHS or any(
        p["inner_hull_buffer_m"] != 0 for p in plans
    ):
        raise ValueError("the exact preregistered five b0 narrow widths are required")
    rows = []
    for p in plans:
        actual = finite_retained_sources(fires, p["demolished_ids"])
        if actual != expected_sources or p["protected_original_roof_node_cleared_count"] != 0:
            raise ValueError("a narrow candidate changes a retained source or clears a core")
        # An operator path across a narrow band is allowed and must be tested.
        rows.append(
            {
                "plan_id": p["plan_id"],
                "demolished_ids": p["demolished_ids"],
                "cleared_physical_cell_ids": p["cleared_physical_cell_ids"],
                "ground_evidence": "frozen_b0_hull_narrow_width_sweep;not_fire_safety_certified",
            }
        )
    return rows


def deduplicate_actions(plans):
    """Merge only exactly identical physical D/G actions, retaining width aliases."""
    groups = {}
    for plan in plans:
        key = content_hash(
            [sorted(plan["demolished_ids"]), sorted(plan["cleared_physical_cell_ids"])]
        )
        groups.setdefault(key, []).append(plan)
    result = []
    for key, variants in groups.items():
        first = deepcopy(min(variants, key=lambda p: p["nominal_ring_width_m"]))
        first["physical_action_sha256"] = key
        first["equivalent_widths_m"] = sorted(p["nominal_ring_width_m"] for p in variants)
        first["equivalent_parameter_plan_ids"] = [p["plan_id"] for p in variants]
        result.append(first)
    return sorted(result, key=lambda p: p["nominal_ring_width_m"])


def prepare(template_path, external_path, output_config, report_prefix, clock_receipt_path):
    start = time.monotonic()
    template_path, external_path, output_config, report_prefix = [
        Path(p).resolve() for p in (template_path, external_path, output_config, report_prefix)
    ]
    clock_receipt_path = Path(clock_receipt_path).resolve()
    clock = read_json(clock_receipt_path)
    if (
        clock.get("schema_version") != "v3-sample-clock-error-path-extension-audit-1"
        or clock.get("remove_exact_added_error_branch_restores_entire_old_file_byte_for_byte")
        is not True
        or file_sha256(ROOT / "src/pyfireca/village/solver_v3.py") != clock.get("new_solver_sha256")
        or clock.get("regression_validation", {}).get("total_passed", 0) < 123
        or clock.get("audit_sha256")
        != content_hash({k: v for k, v in clock.items() if k != "audit_sha256"})
    ):
        raise ValueError("the exact audited success-path-preserving clock fix is required")
    template, external = read_json(template_path), read_json(external_path)
    validate_config(template)
    if (
        template.get("terminal_fire_verification") is not True
        or template.get("terminal_horizon_cap_s") != 172800
    ):
        raise ValueError("the inherited 172800s full-fire template is required")
    reference, _ = read_master_reference(template_path, template)
    if (
        external["reference_master_experiment_sha256"]
        != template["reference_master_experiment_sha256"]
    ):
        raise ValueError("external reference uses a different frozen source/model mesh")
    old_catalog_path = Path(external["vegetation_proposals_path"])
    old_catalog = read_json(old_catalog_path)
    old = [
        p
        for p in old_catalog["plans"]
        if p["inner_hull_buffer_m"] == 0 and p["nominal_ring_width_m"] == 15.01
    ]
    if len(old) != 1:
        raise ValueError("one w15.01 external global-hull geometric reference is required")
    old = old[0]
    seed = next(p["demolished_ids"] for p in template["plans"] if p["plan_id"] == "roof-seed")
    actual = finite_retained_sources(reference["problem"]["fires"], seed)
    initial = {
        str(p): file_sha256(p)
        for p in (
            template_path,
            external_path,
            clock_receipt_path,
            old_catalog_path,
            Path(__file__).resolve(),
            Path(template["roof_seed_receipt_path"]),
            Path(template["roof_seed_search_snapshot_path"]),
            ROOT / "scripts/prepare_heritage_balanced_roof_bands.py",
            ROOT / "scripts/prepare_heritage_protected_union_bands.py",
            ROOT / "scripts/prepare_heritage_all_roof_bands.py",
            ROOT / "scripts/prepare_heritage_optimized_balanced_catalog.py",
        )
    }
    initial[str(clock_receipt_path)] = file_sha256(clock_receipt_path)
    paths = {
        name: Path(str(report_prefix) + suffix)
        for name, suffix in {
            "design": "-design.json",
            "catalog": "-catalog.json",
            "receipt": "-preparation.json",
            "geojson": ".geojson",
        }.items()
    }
    design = {
        "schema_version": "heritage-protected-union-band-design-1",
        "registered_before_parameter_calculation": True,
        "village_id": template["village_id"],
        "inner_offset_m": 0,
        "widths_m": list(WIDTHS),
        "hull_rule": "union_full_core_and_actual_source_roof_polygons;no_convex_hull",
        "original_seed_ids": seed,
        "actual_sources": actual,
        "isolation_is_required": False,
        "cross_partition_distances_and_pairs_are_diagnostic_only": True,
        "geometric_capacity_proxy_is_not_fire_safety_or_loss_prediction": True,
        "all_five_widths_will_be_evaluated_without_outcome_selection": True,
        "proposal_round": "adaptive_local_union_followup_to_long_horizon_P_core_failure",
        "physical_duplicate_actions_are_merged": True,
        "old_global_hull_w15_01_is_not_rescheduled": True,
        "new_CA_checks": 0,
        "native_CA_outcomes_read_for_selection": [],
        "input_sha256": initial,
    }
    immutable(paths["design"], encoded(design))
    bundle, transport, inventory = load_study(Path(template["study_path"]))
    base = resolve_roof_layout(
        bundle.request,
        inventory,
        max_nodes=template["max_nodes"],
        core_overlap_policy=template.get("core_overlap_policy", "reject"),
    )
    plans, features = [], []
    for width in WIDTHS:
        batch, layer, _ = generate_protected_union_bands(
            bundle.request,
            base,
            inventory,
            seed_ids=seed,
            actual_source_ids=actual.values(),
            radius_m=transport.exposure_radius_m,
            offsets=(0,),
            width_m=width,
        )
        plans.extend(batch)
        features.extend(layer)
    raw_plans = plans
    plans = deduplicate_actions(raw_plans)
    rows = checked_union_rows(raw_plans, reference["problem"]["fires"], actual)
    rows = [row for row in rows if row["plan_id"] in {p["plan_id"] for p in plans}]
    certificate = finite_source_catalog_certificate(
        [plan_from_mapping(p) for p in rows],
        reference["problem"]["fires"],
        inventory_sha256=inventory.sha256,
        reference_problem_sha256=reference["problem_sha256"],
    )
    certificate_path = output_config.with_suffix(".finite-source-certificate.json")
    immutable(certificate_path, encoded(certificate))
    catalog = {
        "schema_version": "heritage-protected-union-band-catalog-1",
        "village_id": template["village_id"],
        "plans": plans,
        "all_five_parameter_rows": raw_plans,
        "declared_width_count": 5,
        "distinct_physical_action_count": len(plans),
        "reference_master_problem_sha256": reference["problem_sha256"],
        "finite_source_certificate_sha256": certificate["certificate_sha256"],
        "external_global_hull_geometry_reference_only": {
            "config_path": str(external_path),
            "config_sha256": file_sha256(external_path),
            "plan_id": old["plan_id"],
            "width_m": 15.01,
            "C_construction_action_area_m2": old["C_construction_action_area_m2"],
            "included_in_new_evaluation_plans": False,
            "native_terminal_values_or_statuses_transferred": False,
        },
        "geometry_isolation_not_a_candidate_filter": True,
        "native_CA_outcomes_read_for_selection": [],
        "new_CA_checks": 0,
    }
    immutable(paths["catalog"], encoded(catalog))
    immutable(paths["geojson"], encoded({"type": "FeatureCollection", "features": features}))
    additional = {
        **initial,
        **{
            str(p): file_sha256(p)
            for p in (paths["design"], paths["catalog"], paths["geojson"], certificate_path)
        },
    }
    config = deepcopy(template)
    config.update(
        plans=rows,
        source_coverage_policy="finite_declared_plans",
        max_new_checks=8 * len(plans),
        max_total_checks=8 * len(plans),
        max_wall_time_s=172800.0,
        vegetation_proposals_path=str(paths["catalog"]),
        vegetation_proposals_sha256=file_sha256(paths["catalog"]),
        additional_frozen_inputs_sha256=additional,
        scope="deduplicated_b0_protected_union_band_tradeoffs;no_isolation_filter;fresh_full_fire_CN_A_required",
        clock_compatibility_receipt_path=str(clock_receipt_path),
        clock_compatibility_receipt_sha256=file_sha256(clock_receipt_path),
        required_solver_source_sha256=clock["new_solver_sha256"],
        seed_reverification="old_seed_receipt_is_provenance_only;all_declared_checks_are_new",
    )
    config.pop("roof_replacement_rule", None)
    validate_config(config)
    immutable(output_config, encoded(config))
    audit = zero_ca_validation(output_config, config, certificate)
    if any(
        abs(row["prescribed_source_footprint_area_m2"] - 25) > 1e-8
        for row in audit["plan_source_grass_checks"]
    ):
        raise ValueError("a declared source footprint lost its full 25m2")
    if any(file_sha256(p) != digest for p, digest in initial.items()):
        raise ValueError("a frozen template/helper changed while preparing narrow candidates")
    receipt = {
        "schema_version": "heritage-protected-union-band-preparation-1",
        "config_path": str(output_config),
        "config_sha256": file_sha256(output_config),
        "planned_checks": 8 * len(plans),
        "clock_compatibility_receipt_path": str(clock_receipt_path),
        "clock_compatibility_receipt_sha256": file_sha256(clock_receipt_path),
        "required_producer_solver_sha256": clock["new_solver_sha256"],
        "source_and_parent_guard": audit,
        "cost_and_diagnostics": [
            {
                k: p[k]
                for k in (
                    "plan_id",
                    "nominal_ring_width_m",
                    "planned_roof_ID_count",
                    "cleared_roof_geometry_union_area_m2",
                    "cleared_vegetation_parent_cell_count",
                    "cleared_vegetation_covered_fuel_area_m2",
                    "C_construction_action_area_m2",
                    "conservative_cut_evidence",
                )
            }
            for p in plans
        ],
        "old_global_hull_w15_01_reference_not_rescheduled": True,
        "additional_frozen_inputs_sha256": additional,
        "native_CA_outcomes_read_for_selection": [],
        "new_CA_checks": 0,
        "new_radiation_or_contact_graphs": 0,
        "producer_frozen": False,
        "old_B_bound_generators_or_artifacts_modified": False,
        "elapsed_s": time.monotonic() - start,
    }
    immutable(paths["receipt"], encoded(receipt))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clock-receipt", type=Path, required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--external-reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report-prefix", type=Path, required=True)
    args = parser.parse_args()
    result = prepare(
        args.template, args.external_reference, args.output, args.report_prefix, args.clock_receipt
    )
    print({k: result[k] for k in ("config_path", "planned_checks", "new_CA_checks", "elapsed_s")})


if __name__ == "__main__":
    main()
