"""Prepare two immutable finite-catalog arms with a graph-free, zero-CA audit."""

from __future__ import annotations

import argparse
import hashlib
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import run_heritage_joint_comparison as runner
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.roof_joint import RoofJointEvaluationEngine
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]


def selected_plans(top, control):
    if len(top["plans"]) != 1:
        raise ValueError("the optimized comparison requires the unique preregistered-grid top arm")
    optimized = top["plans"][0]
    original = [
        p
        for p in control["plans"]
        if p["inner_hull_buffer_m"] == 10 and p["nominal_ring_width_m"] == 20
    ]
    if (
        len(original) != 1
        or optimized["inner_hull_buffer_m"] != 0
        or optimized["nominal_ring_width_m"] != 15.01
    ):
        raise ValueError("only optimized r0/w15.01 and original b10/w20 are declared")
    if top["reference_master_problem_sha256"] != control["reference_master_problem_sha256"]:
        raise ValueError("optimized and control references differ")
    result = [optimized, original[0]]
    for plan in result:
        if (
            plan["conservative_cut_evidence"]["certified_no_potential_radiation_or_contact_edge"]
            is not True
            or plan["protected_original_roof_node_cleared_count"] != 0
            or plan["nominal_ring_outside_parent_rectangle_area_m2"] != 0
            or plan["remaining_potential_node_positive_ring_overlap_count"] != 0
        ):
            raise ValueError("a selected arm lost its protected geometry or cut witness")
    return deepcopy(result)


def zero_ca_validation(config_path, config, expected_certificate):
    """Instantiate source/roof geometry only and run the exact vegetation guard."""

    def forbidden(*args, **kwargs):
        raise RuntimeError("production configuration validation must not build a graph or run CA")

    runner_path = Path(runner.__file__).resolve()
    code_digest = file_sha256(runner_path)
    with (
        patch.object(runner, "roof_area_integrated_radiation_graph", forbidden),
        patch.object(runner, "refined_contact_operator", forbidden),
        patch.object(runner.JointComparisonOracle, "engine_for_scenario", forbidden),
        patch.object(RoofJointEvaluationEngine, "simulate", forbidden),
    ):
        oracle, references = runner.make_oracle(config_path, config, code_digest)
        if oracle.finite_source_certificate != expected_certificate:
            raise ValueError("reconstructed finite source certificate differs")
        reference = oracle.reference_master
        if content_hash(oracle.problem["fires"]) != content_hash(reference["problem"]["fires"]):
            raise ValueError("common reference source footprints/refinement changed")
        checker = object.__new__(RoofJointEvaluationEngine)
        checker.bundle, checker.layout = oracle.bundle, oracle.base
        matrix = []
        for name, plan in oracle.plans.items():
            oracle.validate_plan(plan)
            for fire, definition in oracle.fires.items():
                scenario_id = next(s for s, (label, _) in oracle.scenarios.items() if label == fire)
                source = oracle.actual_source_for(plan, scenario_id)
                footprint = definition["footprints"][source]
                point = footprint.geometry.representative_point()
                scenario = EvaluationScenario(
                    fire, (Ignition((point.x, point.y)),), config["horizon_s"]
                )
                nodes = checker._vegetation_nodes(scenario, plan)
                if len(nodes) != len(plan.cleared_physical_cell_ids):
                    raise ValueError("whole-parent vegetation eligibility count differs")
                protected = {t.building_id for t in oracle.inventory.targets} | {source}
                if protected.intersection(plan.demolished_ids):
                    raise ValueError("a production arm removes a protected core or actual source")
                matrix.append(
                    {
                        "plan_id": name,
                        "fire_id": fire,
                        "actual_source_id": source,
                        "eligible_grass_cell_count": len(nodes),
                        "prescribed_source_footprint_area_m2": float(footprint.geometry.area),
                        "refined_ids": list(definition["refined_ids"]),
                    }
                )
        if oracle._engine is not None:
            raise ValueError("zero-CA audit unexpectedly instantiated a physical engine")
    if file_sha256(runner_path) != code_digest:
        raise ValueError("manager changed during zero-CA validation")
    return {
        "validate_config_passed": True,
        "make_oracle_passed": True,
        "plan_source_grass_checks": matrix,
        "all_reference_fire_footprints_and_refined_ids_identical": True,
        "graph_builds": 0,
        "new_CA_checks": 0,
        "scenario_engine_instantiated": False,
        "validation_problem_sha256_not_execution_cache_identity": oracle.problem_sha256,
        "manager_path": str(runner_path),
        "manager_sha256": code_digest,
        "actual_reference_input_count": len(references),
        "reference_master_problem_sha256": reference["problem_sha256"],
    }


def prepare(template_path, top_path, control_path, output_config, receipt_path):
    template_path, top_path, control_path, output_config, receipt_path = [
        Path(p).resolve()
        for p in (template_path, top_path, control_path, output_config, receipt_path)
    ]
    template, top, control = read_json(template_path), read_json(top_path), read_json(control_path)
    template_experiment_path = (
        ROOT / "runs/research/heritage-joint-longcap-batuan-20261111/experiment.json"
    )
    template_experiment = read_json(template_experiment_path)
    if template_experiment["config"] != template or template_experiment[
        "config_sha256"
    ] != file_sha256(template_path):
        raise ValueError("long-cap template changed from its frozen experiment")
    selected = selected_plans(top, control)
    if (
        template.get("terminal_fire_verification") is not True
        or template["terminal_horizon_cap_s"] != 172800
        or template["max_wall_time_s"] != 172800
    ):
        raise ValueError("original 172800s long-cap template is required")
    reference_path = Path(template["reference_master_experiment_path"])
    reference = read_json(reference_path)
    if (
        file_sha256(reference_path) != template["reference_master_experiment_sha256"]
        or content_hash(reference["problem"]) != reference["problem_sha256"]
    ):
        raise ValueError("frozen master reference changed")
    if top["reference_master_problem_sha256"] != reference["problem_sha256"]:
        raise ValueError("geometry catalog is outside the common mesh reference")
    grid_path, design_path = (
        Path(top["reference_geometry_report_path"]),
        Path(top["pre_registered_design_path"]),
    )
    grid, design = read_json(grid_path), read_json(design_path)
    if (
        file_sha256(design_path) != top["pre_registered_design_sha256"]
        or file_sha256(top_path) != grid["top_catalog_sha256"]
        or grid["parameter_grid_complete"] is not True
        or grid["parameter_combinations_evaluated"] != 51
        or selected[0]["plan_id"] not in grid["minimum_C_grid_certificate"]["minimizer_plan_ids"]
        or design["registered_before_parameter_calculation"] is not True
    ):
        raise ValueError("predeclared 51-point geometry selection receipt changed")
    inputs = {
        "top-catalog.json": top_path,
        "control-catalog.json": control_path,
        "grid-design.json": design_path,
        "grid-results.json": grid_path,
        "longcap-template.json": template_path,
        "longcap-experiment.json": template_experiment_path,
        "grid-generator.py": ROOT / "scripts/optimize_heritage_balanced_geometry.py",
        "balanced-band-generator.py": ROOT / "scripts/prepare_heritage_balanced_roof_bands.py",
        "allroof-band-helper.py": ROOT / "scripts/prepare_heritage_all_roof_bands.py",
        "production-preparer.py": Path(__file__).resolve(),
        "seed-receipt.json": Path(template["roof_seed_receipt_path"]),
        "seed-search.json": Path(template["roof_seed_search_snapshot_path"]),
    }
    expected = {
        str(inputs["grid-generator.py"]): grid["generator_sha256"],
        str(inputs["balanced-band-generator.py"]): control["generator_sha256"],
        str(inputs["allroof-band-helper.py"]): control["helper_sha256"],
        str(inputs["seed-receipt.json"]): template["roof_seed_receipt_sha256"],
        str(inputs["seed-search.json"]): template["roof_seed_search_sha256"],
        **reference["references_sha256"],
    }
    for path, digest in expected.items():
        if file_sha256(path) != digest:
            raise ValueError("original geometry generator/seed/physical input changed: " + path)
    sidecars = {name: output_config.with_suffix("." + name) for name in inputs}
    artifacts = {sidecars[name]: path.read_bytes() for name, path in inputs.items()}
    additional = {str(path): hashlib.sha256(data).hexdigest() for path, data in artifacts.items()}
    rows = [
        {
            "plan_id": p["plan_id"],
            "demolished_ids": p["demolished_ids"],
            "cleared_physical_cell_ids": p["cleared_physical_cell_ids"],
            "ground_evidence": (
                "predeclared_geometric_optimized_vs_original_control;not_field_verified"
            ),
        }
        for p in selected
    ]
    certificate = runner.finite_source_catalog_certificate(
        [runner.plan_from_mapping(p) for p in rows],
        reference["problem"]["fires"],
        inventory_sha256=reference["problem"]["inventory_sha256"],
        reference_problem_sha256=reference["problem_sha256"],
    )
    expected_sources = control["finite_source_receipt"]["actual_sources"]
    if any(
        row["actual_source_id"] != expected_sources[row["fire_id"]] for row in certificate["rows"]
    ):
        raise ValueError("selected production arms changed the original actual-source matrix")
    proof_path = output_config.with_suffix(".finite-source-certificate.json")
    artifacts[proof_path] = encoded(certificate)
    additional[str(proof_path)] = hashlib.sha256(artifacts[proof_path]).hexdigest()
    comparison = {
        "schema_version": "heritage-optimized-balanced-two-arm-catalog-1",
        "selection": "predeclared51point_geometry_minC_vs_original_b10w20;not_CA_outcome_selected",
        "plans": selected,
        "original_input_snapshots_sha256": additional.copy(),
        "geometry_C_reduction_m2": selected[1]["C_construction_action_area_m2"]
        - selected[0]["C_construction_action_area_m2"],
        "geometry_improvement_is_not_fire_safety_or_real_loss_improvement": True,
        "finite_source_certificate_sha256": certificate["certificate_sha256"],
    }
    comparison_path = output_config.with_suffix(".comparison-catalog.json")
    artifacts[comparison_path] = encoded(comparison)
    additional[str(comparison_path)] = hashlib.sha256(artifacts[comparison_path]).hexdigest()
    config = deepcopy(template)
    config.update(
        plans=rows,
        source_coverage_policy="finite_declared_plans",
        max_new_checks=16,
        max_total_checks=16,
        max_wall_time_s=172800.0,
        terminal_horizon_cap_s=172800.0,
        vegetation_proposals_path=str(comparison_path),
        vegetation_proposals_sha256=additional[str(comparison_path)],
        roof_seed_receipt_path=str(sidecars["seed-receipt.json"]),
        roof_seed_search_snapshot_path=str(sidecars["seed-search.json"]),
        additional_frozen_inputs_sha256=additional,
        scope="two_finite_geometric_catalog_arms;old_roof_cap_mesh_reference_only;terminal_CN_A_requires_fresh_CA",
        seed_reverification="original_seed_receipt_is_provenance_only;all16_checks_are_new",
        comparison_arm_roles={
            rows[0]["plan_id"]: "geometric_grid_minimum_C",
            rows[1]["plan_id"]: "original_predefined_geometry_control",
        },
    )
    config.pop("roof_replacement_rule", None)
    config.pop("preparation_status", None)
    for key in (
        "study_path",
        "source_fires",
        "source_policy",
        "source_inventory_sha256",
        "candidate_ids",
        "refinement_ids",
        "roof_scales_m",
        "quadrature_spacing_m",
        "max_dt_s",
        "horizon_s",
        "checkpoints_s",
        "core_overlap_policy",
        "source_area_limit_m2",
        "source_area_tolerance_m2",
        "reference_master_experiment_path",
        "reference_master_experiment_sha256",
        "reference_master_config_path",
        "reference_master_config_sha256",
    ):
        if config[key] != template[key]:
            raise ValueError(
                "production catalog changed a reference source/mesh/numerical field: " + key
            )
    runner.validate_config(config)
    artifacts[output_config] = encoded(config)
    if any(path.exists() and path.read_bytes() != data for path, data in artifacts.items()):
        raise ValueError("immutable optimized production input already differs")
    for path, data in artifacts.items():
        immutable(path, data)
    audit = zero_ca_validation(output_config, config, certificate)
    receipt = {
        "schema_version": "heritage-optimized-balanced-production-preparation-1",
        "new_CA_checks": 0,
        "graph_builds": 0,
        "producer_frozen": False,
        "config_path": str(output_config),
        "config_sha256": file_sha256(output_config),
        "planned_checks": 16,
        "arms": 2,
        "source_families": 4,
        "roof_supports": 2,
        "terminal_horizon_cap_s": 172800.0,
        "max_wall_time_s": 172800.0,
        "additional_frozen_inputs_sha256": additional,
        "original_inputs_sha256": {str(path): file_sha256(path) for path in inputs.values()},
        "canonical_finite_source_certificate_sha256": certificate["certificate_sha256"],
        "zero_CA_validation": audit,
        "old_catalogs_drafts_or_caches_modified": False,
        "old_area_bound_not_extended_and_no_global_claim": True,
        "geometry_C_m2_by_plan": {
            p["plan_id"]: p["C_construction_action_area_m2"] for p in selected
        },
        "geometry_capacity_proxy_m2_by_plan": {
            p["plan_id"]: p["inner_remaining_potential_covered_capacity_m2"] for p in selected
        },
        "source_selection_informed_by_new_CA_results": False,
        "preparer_sha256": file_sha256(Path(__file__)),
    }
    immutable(receipt_path, encoded(receipt))
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
        "--top",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-geometry-top-20261111.json",
    )
    parser.add_argument(
        "--control",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-bands-20261111.json",
    )
    parser.add_argument(
        "--output-config",
        type=Path,
        default=ROOT
        / "examples/research/heritage/area-20261110"
        / "batuan-balanced-optimized-longcap-comparison-20261111.json",
    )
    parser.add_argument(
        "--receipt",
        type=Path,
        default=ROOT / "docs/results/heritage-batuan-balanced-optimized-preparation-20261111.json",
    )
    args = parser.parse_args()
    receipt = prepare(args.template, args.top, args.control, args.output_config, args.receipt)
    print(
        {
            "planned_checks": 16,
            "new_CA_checks": 0,
            "graph_builds": 0,
            "config": receipt["config_path"],
            "geometry_costs": receipt["geometry_C_m2_by_plan"],
        }
    )


if __name__ == "__main__":
    main()
