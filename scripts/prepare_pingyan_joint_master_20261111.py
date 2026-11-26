"""Register Pingyan's complete joint action domain without graphs or CA."""

from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path

import run_heritage_joint_master as runner
from prepare_heritage_joint_longcap import encoded, immutable

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]


def prepare(output_config, output_report):
    phase_config_path = (
        ROOT / "examples/research/heritage/area-20261110/pingyan-retained-area-master.json"
    )
    reference_path = ROOT / "runs/research/heritage-retained-area-pingyan-20261110/experiment.json"
    template_path = (
        ROOT
        / "examples/research/heritage/area-20261110"
        / "batuan-joint-action-finite-mask-master-20261111.json"
    )
    clock_path = ROOT / "docs/results/v3-sampling-clock-error-path-audit-20261111.json"
    phase, template, clock = map(read_json, (phase_config_path, template_path, clock_path))
    if file_sha256(ROOT / "src/pyfireca/village/solver_v3.py") != clock["new_solver_sha256"]:
        raise ValueError("the audited fixed sampling-clock source must be active")
    config = deepcopy(template)
    for key in (
        "village_id",
        "place_name",
        "study_path",
        "source_policy",
        "fixed_source_ids",
        "source_fires",
        "source_area_limit_m2",
        "source_area_tolerance_m2",
        "refinement_ids",
        "core_overlap_policy",
        "prior_fixed_problem_seed_receipt",
        "parent_full_config_sha256",
    ):
        config[key] = phase[key]
    config.update(
        reference_phase_master_experiment_path=str(reference_path),
        reference_phase_master_experiment_sha256=file_sha256(reference_path),
        seed_action_plans=[{"action_ids": phase["seed_plans"][i]} for i in (1, 2)],
        horizon_s=172800.0,
        reference_horizon_s=3600.0,
        roof_scales_m=[1.0, 0.5],
        quadrature_spacing_m=0.5,
        max_dt_s=1.0,
        max_new_checks=128,
        max_wall_time_s=172800.0,
        max_mip_time_s=2400.0,
        mip_call_time_limit_s=120.0,
        max_nodes=500000,
        max_radiation_edges=60000000,
        max_matrix_nonzeros=5000000,
        terminal_validation_scope="finite_mask_conditioned",
        clock_compatibility_receipt_path=str(clock_path),
        clock_compatibility_receipt_sha256=file_sha256(clock_path),
        required_solver_source_sha256=clock["new_solver_sha256"],
        clock_receipt_binding="configuration_provenance;verify_frozen_producer_solver_SHA_before_prepare",
        scope=(
            "minimum_joint_construction_C_for_core_protection_in_fixed_finite_mask;"
            "all_inventory_ordinary_roofs_and_all_eligible_whole5m_ground;"
            "same1531_cap_and_original13_refinement;seeds_are_proposals_not_upper_bounds"
        ),
    )
    config.pop("grass_cell_ids", None)
    runner.validate_config(config)
    immutable(output_config, encoded(config))
    sources = {str(p.relative_to(ROOT)): file_sha256(p) for p in runner.source_paths()}
    oracle, references = runner.make_oracle(output_config, config, content_hash(sources))
    reference = read_json(reference_path)
    prefixes = {
        name: list(p.roof_coverage.source_ids) for name, p in oracle.source_coverages.items()
    }
    for name, fire in reference["problem"]["fires"].items():
        expected = [
            i for i in fire["protocol"]["draws"][0]["priority_ids"] if i in fire["footprints"]
        ]
        if prefixes[name] != expected:
            raise ValueError("source area cap changed the immutable phase prefix/mesh")
    report = {
        "schema_version": "heritage-pingyan-joint-master-registration-1",
        "config_path": str(output_config),
        "config_sha256": file_sha256(output_config),
        "native_problem_sha256": oracle.native_problem_sha256,
        "finite_domain_analysis_problem_sha256": oracle.problem_sha256,
        "joint_universe_sha256": oracle.logical_universe.sha256,
        "roof_actions": len(oracle.inventory.candidate_ids),
        "ground_actions": len(oracle.logical_universe.grass_cell_ids),
        "total_actions": len(oracle.candidate_ids),
        "grass_scope": oracle.logical_universe.grass_scope,
        "source_prefixes": prefixes,
        "scenario_order": list(oracle.scenarios),
        "phase_source_cap_and_prepared_source_prefixes_unchanged": True,
        "refinement_seed_ids": config["refinement_ids"],
        "core_overlap_policy": config["core_overlap_policy"],
        "core_ids": [t.building_id for t in oracle.inventory.targets],
        "proposal_seed_C_m2": [
            oracle.logical_universe.area(p["action_ids"]) for p in config["seed_action_plans"]
        ],
        "seed_status": "proposals_only;no_terminal_joint_upper_bound_transferred",
        "reference_input_count": len(references),
        "source_files_sha256": sources,
        "source_manifest_sha256": content_hash(sources),
        "required_producer_solver_sha256": clock["new_solver_sha256"],
        "clock_receipt_path": str(clock_path),
        "clock_receipt_sha256": file_sha256(clock_path),
        "new_CA_checks": 0,
        "radiation_or_contact_graphs_built": 0,
        "scenario_partition_capacity_preflight_performed": False,
        "producer_frozen": False,
    }
    if sources != {str(p.relative_to(ROOT)): file_sha256(p) for p in runner.source_paths()}:
        raise ValueError("source changed during the graph-free registration")
    immutable(output_report, encoded(report))
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    report = prepare(args.config.resolve(), args.report.resolve())
    print(
        {
            k: report[k]
            for k in (
                "config_path",
                "roof_actions",
                "ground_actions",
                "total_actions",
                "new_CA_checks",
            )
        }
    )


if __name__ == "__main__":
    main()
