"""Audit two strictly feasible plans from one native campaign on every matched case."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from tempfile import TemporaryDirectory

import summarize_heritage_joint_pareto as native


def paired_data(report, reference_id, candidate_id):
    if reference_id == candidate_id:
        raise ValueError("two distinct physical plans are required")
    archive = report["archive"]
    feasible = {p["plan_id"]: p for p in archive["fully_evaluated_feasible_plans"]}
    if reference_id not in feasible or candidate_id not in feasible:
        raise ValueError("both plans require complete strict feasible evidence")
    reference, candidate = feasible[reference_id], feasible[candidate_id]
    scenarios = archive["comparison_protocol"]["scenario_ids"]
    checks = {(c["plan_id"], c["scenario_id"]): c for c in report["native_checks"]}
    matched = []
    paired_fields = (
        "source_id",
        "source_budget",
        "source_footprint_sha256",
        "common_layout_sha256",
        "frozen_integrated_node_mask_sha256",
    )
    for slot, scenario in enumerate(scenarios, 1):
        values, evidence = [], []
        for plan in (reference, candidate):
            row = checks[plan["plan_id"], scenario]
            if not (
                row["status"] == "passed"
                and row["terminal_complete"]
                and row["certificate_observation_bound"]
                and row["invalid_reasons"] == []
                and row["core_ignition_observed"] is False
            ):
                raise ValueError("strict terminal status differs from the complete archive")
            path = Path(row["manifest"])
            if native.file_sha256(path) != row["manifest_sha256"]:
                raise ValueError("a native manifest changed after validation")
            manifest = native.read_json(path)
            file = path.parent / "check.json"
            if native.file_sha256(file) != manifest["files_sha256"][file.name]:
                raise ValueError("a native source or mesh binding changed after validation")
            evidence.append(native.read_json(file)["evidence"])
            observed = plan["scenario_observations"][scenario]
            values.append(
                {
                    "N_burned_owner_bounds": observed["N_burned_out"],
                    "A_burned_m2": observed["A_burned_out_m2"],
                    "manifest_sha256": row["manifest_sha256"],
                }
            )
        if any(evidence[0][key] != evidence[1][key] for key in paired_fields):
            raise ValueError("the native actual source, footprint, budget or mesh differs")
        a, b = values
        matched.append(
            {
                "registered_case_ordinal": slot,
                "case_identity_sha256": native.content_hash(scenario),
                "reference": a,
                "candidate": b,
                "candidate_minus_reference_A_m2": b["A_burned_m2"] - a["A_burned_m2"],
                "paired_N_interval_equal": a["N_burned_owner_bounds"] == b["N_burned_owner_bounds"],
            }
        )
    cost_saved = reference["cleared_area_m2"] - candidate["cleared_area_m2"]
    return {
        "native_problem_sha256": report["native_comparison_problem_sha256"],
        "native_experiment_sha256": report["experiment_sha256"],
        "native_source_manifest_sha256": native.content_hash(
            report["native_snapshot_source_sha256"]
        ),
        "native_input_manifest_sha256": native.content_hash(report["physical_references_sha256"]),
        "paired_and_verified_fields": list(paired_fields),
        "matched_complete_count": len(matched),
        "matched": matched,
        "plans": {
            p["plan_id"]: {
                "C_m2": p["cleared_area_m2"],
                "worst_N_burned_owner_bounds": p["worst_case"]["N_burned_out"],
                "worst_A_burned_m2": p["worst_case"]["A_burned_out_m2"],
            }
            for p in (reference, candidate)
        },
        "reference_plan": reference_id,
        "candidate_plan": candidate_id,
        "C_saving_m2": cost_saved,
        "C_saving_fraction": cost_saved / reference["cleared_area_m2"]
        if reference["cleared_area_m2"] > 0
        else None,
        "paired_N_equal_and_A_equal_within_1e_minus8_m2": all(
            r["paired_N_interval_equal"]
            and math.isclose(r["candidate_minus_reference_A_m2"], 0.0, abs_tol=1e-8)
            for r in matched
        ),
        "candidate_dominates_reference_on_C_Nupper_A": candidate_id
        in reference["upper_bound_dominated_by"],
        "candidate_robustly_dominates_under_N_interval_uncertainty": candidate_id
        in reference["robustly_dominated_by"],
        "global_frontier_certified": False,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--reference-plan", required=True)
    parser.add_argument("--candidate-plan", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    run, output = args.run_root.resolve(), args.output.resolve()
    if output.is_relative_to(run) or output.exists():
        raise ValueError("the result needs a fresh path outside the native archive")
    with TemporaryDirectory() as tmp:
        report = native.summarize(run, args.config.resolve(), Path(tmp) / "strict.json")
    receipt = {
        "schema_version": "heritage-native-strict-paired-plan-comparison-1",
        "method_sha256": native.file_sha256(__file__),
        "strict_reader_sha256": native.file_sha256(native.__file__),
        **paired_data(report, args.reference_plan, args.candidate_plan),
    }
    receipt["receipt_sha256"] = native.content_hash(receipt)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(receipt, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"matched": receipt["matched_complete_count"], "output": str(output)}))


if __name__ == "__main__":
    main()
