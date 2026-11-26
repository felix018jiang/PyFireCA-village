"""Verify a three-way matched physical-source comparison and tabulate its finite frontier."""

import argparse
import hashlib
from pathlib import Path

from audit_heritage_paired_burnout import patch_observation
from prepare_heritage_joint_longcap import encoded, immutable
from summarize_heritage_joint_pareto import content_hash, file_sha256, read_json

PLANS = (
    "roof-seed-protected-union-ring-b0-w2.5",
    "local-band-with-observed-clearance-subset",
    "roof-seed-protected-union-ring-b0-w15.01",
)
PAIR = (
    "source_id",
    "source_budget",
    "source_footprint_sha256",
    "common_layout_sha256",
    "frozen_integrated_node_mask_sha256",
)


def table(path):
    merge = read_json(path)
    seal = (
        "composition_receipt_sha256"
        if "composition_receipt_sha256" in merge
        else "merge_receipt_sha256"
    )
    if merge.get("temporal_mode") != "strict" or merge[seal] != content_hash(
        {k: v for k, v in merge.items() if k != seal}
    ):
        raise ValueError("sealed strict cross-catalogue native equivalence audit required")
    a = merge["archive"]
    eq = merge["physical_equivalence_certificate"]
    scenarios = eq["scenario_ids"]
    if len(scenarios) != 8 or set(eq["verified_saved_baseline_partitions"]) != set(scenarios):
        raise ValueError("all eight scenario partitions must be available")
    act = {}
    for plan in a["fully_evaluated_feasible_plans"]:
        aliases = plan.get("plan_aliases", [plan])
        for native_id in {alias["native_plan_id"] for alias in aliases} & set(PLANS):
            if native_id in act:
                raise ValueError("native name refers to multiple physical interventions")
            act[native_id] = plan
    if set(act) != set(PLANS):
        raise ValueError("all three intervention plans must genuinely be strictly closed-feasible")
    records = {
        (v["plan_id"], v["scenario_id"]): v
        for r in merge["independently_reverified_native_reports"]
        for v in r["native_checks"]
        if v["plan_id"] in PLANS and v.get("manifest")
    }
    if len(records) != 24:
        raise ValueError("strict three-way comparison expects 24 independent native case manifests")
    cases = []
    for ordinal, scenario in enumerate(scenarios, 1):
        vals = []
        evidence = []
        for p in PLANS:
            row = records[p, scenario]
            manifest = Path(row["manifest"])
            digest = row["manifest_sha256"]
            if (
                file_sha256(manifest) != digest
                or row["status"] != "passed"
                or not row["terminal_complete"]
                or row["invalid_reasons"]
                or row["core_ignition_observed"]
                or not row["certificate_observation_bound"]
            ):
                raise ValueError("changed manifest or invalid strict terminal result")
            files = read_json(manifest)["files_sha256"]
            check = manifest.parent / "check.json"
            loss = manifest.parent / "fire-loss.json"
            if file_sha256(check) != files[check.name] or file_sha256(loss) != files[loss.name]:
                raise ValueError("native source or physical loss bytes changed")
            raw = read_json(loss)
            observed = act[p]["scenario_observations"][scenario]
            evidence.append(read_json(check)["evidence"])
            N = raw["physical_remaining_roof_annotation_counts"][
                "burned_out_annotation_attribution_count"
            ]
            A = raw["physical"]["all"]["cumulative_burned_out_covered_area_m2"]
            if observed["N_burned_out"] != N or abs(observed["A_burned_out_m2"] - A) > 1e-8:
                raise ValueError("original-owner attribution or all-domain burnout mismatch")
            e = evidence[-1]
            certificate = e["terminal_verification"]["temporal_closure_certificate"]
            patch = patch_observation(
                {
                    "status": "passed",
                    "observation": observed,
                    "binding": {
                        "manifest_path": str(manifest),
                        "manifest_sha256": digest,
                        "native_certificate_sha256": certificate["certificate_sha256"],
                        "native_actual_source": {key: e[key] for key in PAIR[:3]},
                    },
                },
                eq["verified_saved_baseline_partitions"][scenario],
            )
            vals.append(
                {
                    "N_original_roof_owner_bounds": N,
                    "A_burned_m2": A,
                    "manifest_sha256": digest,
                    "burned_mask_sha256": hashlib.sha256(patch["mask"].tobytes()).hexdigest(),
                    "original_owner_profile_sha256": patch["owner_profile"],
                }
            )
        if any(evidence[0][k] != other[k] for k in PAIR for other in evidence[1:]):
            raise ValueError("actual source, geometry, fuel budget or common native mesh differs")
        cases.append({"registered_case_ordinal": ordinal, "in_plan_order": vals})

    def dominates(x, y):
        return all(a <= b for a, b in zip(x, y, strict=True)) and any(
            a < b for a, b in zip(x, y, strict=True)
        )

    vectors = {
        p: (
            act[p]["cleared_area_m2"],
            act[p]["worst_case"]["N_burned_out"]["upper"],
            act[p]["worst_case"]["A_burned_out_m2"],
        )
        for p in PLANS
    }
    frontier = [
        p for p in PLANS if not any(dominates(v, vectors[p]) for q, v in vectors.items() if q != p)
    ]
    rows = [
        {
            "plan_id": p,
            "C_m2": vectors[p][0],
            "worst_N_original_owner_bounds": act[p]["worst_case"]["N_burned_out"],
            "worst_A_burned_m2": vectors[p][2],
            "strict_closed": 8,
            "in_tested_three_plan_nondominated_set": p in frontier,
        }
        for p in PLANS
    ]
    return {
        "schema_version": "three-policy-matched-registered-scenario-optimization-1",
        "merged_native_physical_equivalence_sha256": eq["certificate_sha256"],
        "merge_file_sha256": file_sha256(path),
        "method_sha256": file_sha256(__file__),
        "source_budget_and_physical_mesh_verified_fields": PAIR,
        "plan_order": PLANS,
        "tested_plans": rows,
        "paired_observations": cases,
        "non_dominated_plans_among_three_C_Nupper_A": frontier,
        "subset_dominates_wide_in_C_Nupper_A": dominates(vectors[PLANS[1]], vectors[PLANS[2]]),
        "A_and_N_equal_per_case_for_subset_and_wide": all(
            abs(k["in_plan_order"][1]["A_burned_m2"] - k["in_plan_order"][2]["A_burned_m2"]) < 1e-8
            and k["in_plan_order"][1]["N_original_roof_owner_bounds"]
            == k["in_plan_order"][2]["N_original_roof_owner_bounds"]
            for k in cases
        ),
        "subset_and_wide_identical_burned_patches_and_original_owner_losses": all(
            k["in_plan_order"][1][field] == k["in_plan_order"][2][field]
            for k in cases
            for field in ("burned_mask_sha256", "original_owner_profile_sha256")
        ),
        "independent_source_draws_in_this_refinement": 0,
        "global_optimality_proven": False,
        "origin_native_evaluations_by_original_campaign": {
            r["run_root"]: sum(
                v["plan_id"] in PLANS and bool(v.get("manifest")) for v in r["native_checks"]
            )
            for r in merge["independently_reverified_native_reports"]
        },
        "new_CA_checks": 0,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--merged", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = table(args.merged)
    result["receipt_sha256"] = content_hash(result)
    immutable(args.output, encoded(result))
    print(result["tested_plans"])
