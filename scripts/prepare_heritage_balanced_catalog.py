"""Write an immutable 24-check balanced-catalog draft, without a producer or CA."""

from __future__ import annotations

import argparse
import hashlib
from copy import deepcopy
from pathlib import Path

import run_heritage_joint_comparison as runner
from prepare_heritage_joint_longcap import digest, encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import read_json

ROOT = Path(__file__).resolve().parents[1]
PHYSICAL_KEYS = (
    "study_path",
    "source_policy",
    "source_inventory_sha256",
    "source_fires",
    "candidate_ids",
    "reference_master_config_path",
    "reference_master_config_sha256",
    "reference_master_experiment_path",
    "reference_master_experiment_sha256",
    "refinement_ids",
    "roof_scales_m",
    "quadrature_spacing_m",
    "max_dt_s",
    "horizon_s",
    "core_overlap_policy",
    "source_area_limit_m2",
    "source_area_tolerance_m2",
    "checkpoints_s",
)


def prepare(template_path, catalog_path, output_config, receipt_path, *, template_experiment_path):
    paths = [
        Path(p).resolve()
        for p in (
            template_path,
            catalog_path,
            output_config,
            receipt_path,
            template_experiment_path,
        )
    ]
    template_path, catalog_path, output_config, receipt_path, template_experiment_path = paths
    runner_path = Path(runner.__file__).resolve()
    runner_sha = digest(runner_path)
    template, catalog = read_json(template_path), read_json(catalog_path)
    old_experiment = read_json(template_experiment_path)
    if old_experiment["config"] != template or old_experiment["config_sha256"] != digest(
        template_path
    ):
        raise ValueError("long-cap template differs from its frozen experiment")
    if (
        template.get("terminal_fire_verification") is not True
        or template["terminal_horizon_cap_s"] != 172800
        or template["max_wall_time_s"] != 172800
        or len(template["source_fires"]) != 4
        or template.get("new_independent_fire_draw_count") != 0
    ):
        raise ValueError("the frozen four-family 172800 s long-cap template is required")
    # Preserve semantics when a new configuration is placed in another directory.
    template = deepcopy(template)
    for key, value in tuple(template.items()):
        if key.endswith("_path") and isinstance(value, str):
            template[key] = str((template_path.parent / value).resolve())
    for fire in template["source_fires"]:
        fire["protocol_path"] = str((template_path.parent / fire["protocol_path"]).resolve())
    reference_path = Path(template["reference_master_experiment_path"])
    reference = read_json(reference_path)
    if (
        digest(reference_path) != template["reference_master_experiment_sha256"]
        or content_hash(reference["problem"]) != reference["problem_sha256"]
        or catalog.get("schema_version") != "heritage-balanced-core-source-ring-catalog-1"
        or catalog["reference_master_problem_sha256"] != reference["problem_sha256"]
        or catalog["reference_master_experiment_sha256"] != digest(reference_path)
        or catalog["inventory_sha256"] != reference["problem"]["inventory_sha256"]
        or catalog["inventory_sha256"] != template["source_inventory_sha256"]
        or catalog.get("new_CA_checks") != 0
        or catalog.get("outcome_files_read_for_design") != []
        or len(catalog["plans"]) != 3
        or {p["inner_hull_buffer_m"] for p in catalog["plans"]} != {10, 20, 30}
    ):
        raise ValueError("balanced geometry catalog differs from its immutable reference")
    references = dict(reference["references_sha256"])
    references.update(
        {
            str(template_path): digest(template_path),
            str(catalog_path): digest(catalog_path),
            str(reference_path): digest(reference_path),
            str(template_experiment_path): digest(template_experiment_path),
        }
    )
    for path_key, hash_key in (
        ("reference_master_config_path", "reference_master_config_sha256"),
        ("roof_seed_receipt_path", "roof_seed_receipt_sha256"),
        ("roof_seed_search_snapshot_path", "roof_seed_search_sha256"),
    ):
        references[template[path_key]] = template[hash_key]
    if catalog.get("geojson_path"):
        references[catalog["geojson_path"]] = catalog["geojson_sha256"]
    for path, expected in references.items():
        if digest(path) != expected:
            raise ValueError(
                "catalog/template/frozen input or original seed receipt changed: " + path
            )
    seed = set(catalog["original_seed_ids"])
    expected_sources = catalog["finite_source_receipt"]["actual_sources"]
    protected = set(catalog["core_ids"]) | set(expected_sources.values())
    rows = []
    for row in sorted(catalog["plans"], key=lambda p: p["inner_hull_buffer_m"]):
        ids, cells = row["demolished_ids"], row["cleared_physical_cell_ids"]
        cut = row["conservative_cut_evidence"]
        if (
            not seed <= set(ids)
            or set(ids) & protected
            or row["finite_catalog_actual_sources"] != expected_sources
            or row["first_retained_sources_match_original_seed"] is not True
            or row["protected_original_roof_node_cleared_count"] != 0
            or row["core_and_actual_source_geometries_inside_inner"] is not True
            or cut["certified_no_potential_radiation_or_contact_edge"] is not True
            or cut["potential_unordered_inner_outer_pairs_within_radiation_support"] != 0
            or cut["minimum_inner_outer_physical_polygon_distance_m"]
            <= cut["radiation_support_radius_m"]
            or row["nominal_ring_outside_parent_rectangle_area_m2"] != 0
            or row["remaining_potential_node_positive_ring_overlap_count"] != 0
            or row["cleared_vegetation_ground_footprint_area_m2"] != 25 * len(cells)
        ):
            raise ValueError(
                "a compact candidate loses its cut, protected source, or action-area proof"
            )
        rows.append(
            {
                "plan_id": row["plan_id"],
                "demolished_ids": ids,
                "cleared_physical_cell_ids": cells,
                "ground_evidence": "predeclared_core_source_hull_ring;not_field_verified",
            }
        )
    plans = [runner.plan_from_mapping(row) for row in rows]
    proof = runner.finite_source_catalog_certificate(
        plans,
        reference["problem"]["fires"],
        inventory_sha256=catalog["inventory_sha256"],
        reference_problem_sha256=reference["problem_sha256"],
    )
    if len(proof["rows"]) != 12 or any(
        row["actual_source_id"] != expected_sources[row["fire_id"]] for row in proof["rows"]
    ):
        raise ValueError("finite candidate source matrix differs from the geometry receipt")
    catalog_snapshot = output_config.with_suffix(".catalog.json")
    proof_path = output_config.with_suffix(".finite-source-certificate.json")
    targets = [output_config, receipt_path, catalog_snapshot, proof_path]
    if len(set(targets)) != 4 or any(p in references for p in map(str, targets)):
        raise ValueError("draft outputs must be independent of immutable original inputs")
    new = template | {
        "plans": rows,
        "source_coverage_policy": "finite_declared_plans",
        "max_new_checks": 24,
        "max_total_checks": 24,
        "terminal_horizon_cap_s": 172800.0,
        "max_wall_time_s": 172800.0,
        "vegetation_proposals_path": str(catalog_snapshot),
        "vegetation_proposals_sha256": digest(catalog_path),
        "finite_source_catalog_certificate_path": str(proof_path),
        "finite_source_catalog_certificate_sha256": proof["certificate_sha256"],
        "finite_source_catalog_certificate_file_sha256": hashlib.sha256(encoded(proof)).hexdigest(),
        "preparation_status": "immutable_draft_awaiting_separate_producer_freeze",
        "scope": (
            "only_three_declared_balanced_C_N_A_candidates;"
            "old_roof_cap_is_mesh_reference_not_admissibility;no_global_certificate"
        ),
        "seed_reverification": (
            "original_seed_receipt_is_provenance_only;all_new_arms_need_fresh_terminal_checks"
        ),
    }
    new.pop("roof_replacement_rule", None)
    for key in PHYSICAL_KEYS:
        if new.get(key) != template.get(key):
            raise ValueError("balanced draft changed a frozen source/mesh/numerical field: " + key)
    runner.validate_config(new)
    matrix = [
        {
            k: row[k]
            for k in (
                "plan_id",
                "fire_id",
                "draw_id",
                "actual_source_id",
                "source_footprint_sha256",
                "removed_priority_prefix",
            )
        }
        for row in proof["rows"]
    ]
    receipt = {
        "schema_version": "heritage-balanced-finite-catalog-draft-1",
        "new_CA_checks": 0,
        "geometry_preflight_executed": False,
        "producer_snapshot_frozen": False,
        "template_config_path": str(template_path),
        "template_config_sha256": digest(template_path),
        "template_experiment_path": str(template_experiment_path),
        "template_experiment_sha256": digest(template_experiment_path),
        "source_geometry_catalog_path": str(catalog_path),
        "source_geometry_catalog_sha256": digest(catalog_path),
        "catalog_snapshot_path": str(catalog_snapshot),
        "catalog_snapshot_sha256": digest(catalog_path),
        "output_config_path": str(output_config),
        "output_config_sha256": hashlib.sha256(encoded(new)).hexdigest(),
        "finite_source_certificate_path": str(proof_path),
        "finite_source_certificate_sha256": proof["certificate_sha256"],
        "finite_source_certificate_file_sha256": new[
            "finite_source_catalog_certificate_file_sha256"
        ],
        "frozen_input_reference_sha256": references,
        "planned_CA_checks": 24,
        "declared_joint_arms": 3,
        "source_families": 4,
        "roof_supports": 2,
        "new_independent_fire_draws": 0,
        "finite_first_retained_matrix": matrix,
        "terminal_horizon_cap_s": 172800.0,
        "max_wall_time_s": 172800.0,
        "source_refinement_mesh_and_numerics_preserved": True,
        "old_roof_cap_is_admissibility_for_this_catalog": False,
        "all_new_arms_require_independent_evaluation_from_zero": True,
        "old_cache_or_spent_budget_transferred": False,
        "whole_fire_losses_only_when_strictly_closed": True,
        "global_optimality_or_frontier_completeness_claimed": False,
        "changed_configuration_keys": sorted(
            k for k in set(template) | set(new) if template.get(k) != new.get(k)
        ),
        "draft_manager_path": str(runner_path),
        "draft_manager_sha256": runner_sha,
        "preparer_sha256": digest(Path(__file__)),
    }
    if digest(runner_path) != runner_sha:
        raise ValueError("draft manager changed during source-proof construction; retry at zero CA")
    # Check every existing output before writing any new file; never partially replace a receipt.
    artifacts = [
        (catalog_snapshot, catalog_path.read_bytes()),
        (proof_path, encoded(proof)),
        (output_config, encoded(new)),
        (receipt_path, encoded(receipt)),
    ]
    if any(p.exists() and p.read_bytes() != data for p, data in artifacts):
        raise ValueError("immutable balanced draft already differs")
    for path, data in artifacts:
        immutable(path, data)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--template",
        type=Path,
        default=ROOT
        / "examples/research/heritage/area-20261110/batuan-joint-longcap-comparison-20261111.json",
    )
    parser.add_argument(
        "--template-experiment",
        type=Path,
        default=ROOT / "runs/research/heritage-joint-longcap-batuan-20261111/experiment.json",
    )
    parser.add_argument(
        "--catalog",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-bands-20261111.json",
    )
    parser.add_argument(
        "--output-config",
        type=Path,
        default=ROOT
        / "examples/research/heritage/area-20261110"
        / "batuan-balanced-finite-longcap-comparison-20261111.draft.json",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-finite-catalog-draft-20261111.json",
    )
    args = parser.parse_args()
    receipt = prepare(
        args.template,
        args.catalog,
        args.output_config,
        args.receipt,
        template_experiment_path=args.template_experiment,
    )
    print(
        {
            "new_CA_checks": 0,
            "planned_checks": receipt["planned_CA_checks"],
            "producer_snapshot_frozen": False,
            "config": receipt["output_config_path"],
        }
    )


if __name__ == "__main__":
    main()
