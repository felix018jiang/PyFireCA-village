"""Bind independent full-fire replay to the jointly optimized action and a frozen source mesh."""

import argparse
from pathlib import Path

from prepare_heritage_joint_longcap import encoded, immutable
from run_heritage_joint_comparison import (
    finite_source_catalog_certificate,
    plan_from_mapping,
    read_master_reference,
    validate_config,
)

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def prepare(village):
    root = Path(__file__).resolve().parents[1]
    base = root / "examples/research/heritage/area-20261110"
    reports = root / "docs/results"
    master_path = base / f"{village}-compartment-source-reference-20261111.json"
    master_run = root / f"runs/research/heritage-compartment-source-reference-{village}-20261111"
    master_exp = master_run / "experiment.json"
    parent, master = read_json(master_exp), read_json(master_path)
    if parent["config_sha256"] != file_sha256(master_path):
        raise ValueError("reference master configuration mismatch")
    total_path = reports / f"heritage-total-protection-and-loss-{village}-20261111.json"
    result = read_json(total_path)
    if result["receipt_sha256"] != content_hash(
        {k: v for k, v in result.items() if k != "receipt_sha256"}
    ):
        raise ValueError("combined computation changed")
    chosen = next(
        r for r in result["optimized_total_construction"] if r["core_energy_ceiling"] == 0.99
    )
    row = {
        "plan_id": "joint-core-and-village-loss",
        "demolished_ids": chosen["demolished_ids"],
        "cleared_physical_cell_ids": chosen["cleared_physical_cell_ids"],
        "ground_evidence": "conditional_energy_and_compartment_optimization_whole_ground_cells",
    }
    proof = finite_source_catalog_certificate(
        [plan_from_mapping(row)],
        parent["problem"]["fires"],
        inventory_sha256=parent["problem"]["inventory_sha256"],
        reference_problem_sha256=parent["problem_sha256"],
    )
    # Verify every retained source and geometry against the original cohorts used by the loss proof.
    reference_plans = [
        (
            root / f"runs/research/heritage-thermal-and-geometric-pareto-{village}-20261111",
            "minimum-thermal-energy-clearance",
        )
    ]
    if village == "batuan":
        p = root / "runs/research/heritage-batuan-thermal-vs-D9-revealed-sources-20261111"
        reference_plans.append(
            (p, read_json(p / "experiment.json")["config"]["plans"][0]["plan_id"])
        )
    expected = {}
    for run, plan_id in reference_plans:
        exp = read_json(run / "experiment.json")
        for name, fire in exp["problem"]["fires"].items():
            s = next(
                x["actual_source_id"]
                for x in exp["source_realizations"]
                if x["plan_id"] == plan_id and x["scenario_id"] == name + "-h1"
            )
            expected[name] = (s, content_hash(fire["footprints"][s]))
    verified = {
        r["fire_id"]: (r["actual_source_id"], r["source_footprint_sha256"]) for r in proof["rows"]
    }
    if verified != expected or len(expected) != result["fixed_25m2_source_budget_count"]:
        raise ValueError(
            "common reference misses a registered source identity or physical footprint"
        )
    proof_path = base / f"{village}-joint-loss-native-20261111.finite-source-certificate.json"
    immutable(proof_path, encoded(proof))
    settings = read_json(base / f"{village}-thermal-and-geometric-pareto-20261111.json")
    config = {
        k: settings[k]
        for k in (
            "schema_version",
            "village_id",
            "study_path",
            "source_policy",
            "horizon_s",
            "checkpoints_s",
            "terminal_fire_verification",
            "terminal_horizon_cap_s",
            "roof_scales_m",
            "quadrature_spacing_m",
            "max_dt_s",
            "core_overlap_policy",
            "minimum_free_disk_GB",
            "required_solver_source_sha256",
        )
    }
    refs = [
        total_path,
        proof_path,
        Path(__file__).resolve(),
        reports / f"heritage-first-escape-mandatory-{village}-20261111.json",
        reports / f"heritage-first-escape-compartment-{village}-20261111.json",
        root / "scripts/optimize_heritage_protection_and_loss.py",
        *(p / "experiment.json" for p, _ in reference_plans),
    ]
    planned = len(master["source_fires"]) * len(master["roof_scales_m"])
    config.update(
        plans=[row],
        refinement_ids=master["refinement_ids"],
        source_fires=master["source_fires"],
        source_coverage_policy="finite_declared_plans",
        candidate_ids=parent["problem"]["candidate_ids"],
        reference_master_config_path=str(master_path),
        reference_master_config_sha256=file_sha256(master_path),
        reference_master_experiment_path=str(master_exp),
        reference_master_experiment_sha256=file_sha256(master_exp),
        source_area_limit_m2=master["source_area_limit_m2"],
        source_area_tolerance_m2=master["source_area_tolerance_m2"],
        max_nodes=master["max_nodes"],
        max_radiation_edges=master["max_radiation_edges"],
        max_new_checks=planned,
        max_total_checks=planned,
        max_wall_time_s=172800,
        new_independent_fire_draw_count=0,
        additional_frozen_inputs_sha256={str(p): file_sha256(p) for p in refs},
        scope=(
            "joint core protection and registered village loss bounds; "
            "new native full-fire checks on the common source mesh"
        ),
    )
    validate_config(config)
    path = base / f"{village}-joint-loss-native-20261111.json"
    read_master_reference(path, config)
    immutable(path, encoded(config))
    return path, planned


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--village", choices=("batuan", "pingyan"), required=True)
    args = parser.parse_args()
    print(*prepare(args.village))
