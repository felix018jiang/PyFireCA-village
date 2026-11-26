"""Register supplementary common layouts under the fixed native graph limit."""

import argparse
import json
from pathlib import Path

from summarize_heritage_joint_pareto import file_sha256, read_json


def prepare(config_path, geometry_path, producer_path, output, receipt):
    config, measured = read_json(config_path), read_json(geometry_path)
    arms = [p["plan_id"] for p in config["plans"]]
    fires = [f["fire_id"] for f in config["source_fires"]]
    scales = config["roof_scales_m"]
    total_groups = {(i, scale) for i in range(1, len(fires) + 1) for scale in scales}
    rows = measured["rows"]
    if (
        len(arms) != 3
        or len(total_groups) != 8
        or len(rows) != 8
        or {(r["draw_ordinal"], r["scale_m"]) for r in rows} != total_groups
        or not measured["supplemental_diagnostics_for_all_eight_common_layouts"]
    ):
        raise ValueError("all eight registered common groups and all three arms are required")
    groups, selected, ambiguous = [], [], []
    for row in rows:
        stages = row["actual_graph_stage_bound_by_plan"]
        if set(stages) != set(arms) or set(row["physical_support_by_plan"]) != set(arms):
            raise ValueError("a fixed arm's geometry or physical source is missing")
        limit = config["max_radiation_edges"]
        stages_ok = {}
        for arm in arms:
            if set(stages[arm]) != {"after_roofs_before_grass", "after_roofs_and_grass"}:
                raise ValueError("both native builder stages must be accounted for")
            stages_ok[arm] = all(max(v.values()) <= limit for v in stages[arm].values())
        hard = row["nodes"] > config["max_nodes"] or any(
            v["raw_point_edges"] > limit for pair in stages.values() for v in pair.values()
        )
        eligible = row["nodes"] <= config["max_nodes"] and all(stages_ok.values())
        identity = fires[row["draw_ordinal"] - 1] + f"-h{row['scale_m']:g}"
        if not eligible and not hard:
            ambiguous.append((row["draw_ordinal"], row["scale_m"]))
        if eligible:
            selected.extend({"scenario_id": identity, "plan_id": arm} for arm in arms)
        groups.append(
            {
                "draw_ordinal": row["draw_ordinal"],
                "scale_m": row["scale_m"],
                "source_supports_by_arm_verified": True,
                "intact_preflight_passed": row["preflight_passed"],
                "actual_stage_bound_passed_by_arm": stages_ok,
                "true_node_or_point_limit_violation_for_at_least_one_arm": hard,
                "admitted_for_all_arms": eligible,
            }
        )
    if ambiguous:
        raise ValueError(f"exact quadrature counting needed for unresolved groups {ambiguous}")
    if not selected:
        raise ValueError("no common registered group satisfies the native resource limit")
    config = dict(config)
    config["execution_case_selection"] = selected
    config["supplemental_geometry_receipt"] = {
        "path": str(geometry_path),
        "sha256": file_sha256(geometry_path),
    }
    provenance = {
        str(path): file_sha256(path)
        for path in (config_path, geometry_path, producer_path, Path(__file__).resolve())
    }
    config["additional_frozen_inputs_sha256"] = {
        **config["additional_frozen_inputs_sha256"],
        **provenance,
    }
    config["scope"] = (
        "supplementary_capacity_qualified_common_layouts;original_primary_resource_unknown;"
        "all_24_expected;missing_cases_never_promoted;no_new_draws_no_regridding"
    )
    # Keep the accumulated original 24-slot budget and all eight criteria per plan.
    if output.exists() or receipt.exists():
        raise ValueError("supplemental configuration and receipt must use new paths")
    with output.open("x") as stream:
        json.dump(config, stream, indent=2, allow_nan=False)
        stream.write("\n")
    registration = {
        "schema_version": "supplementary-three-arm-native-capacity-selection-1",
        "method": "precomputed conservative geometry verified before CA; native producer "
        "independently recomputes each admissibility check and retains native hard guards",
        "inputs_sha256": provenance,
        "supplemental_config_sha256": file_sha256(output),
        "source_draws_total": 4,
        "new_source_draws": 0,
        "expected_checks_per_plan": 8,
        "expected_total": 24,
        "selected_total": len(selected),
        "groups": groups,
        "registered_intact_primary_status": "unknown_resource_stop_zero_CA",
        "new_CA_checks_in_registration": 0,
        "intact_registered_state_and_native_artifacts_modified": False,
    }
    with receipt.open("x") as stream:
        json.dump(registration, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"original_expected": 24, "selected": len(selected)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("config", "geometry", "producer-manifest", "output", "receipt"):
        parser.add_argument("--" + key, type=Path, required=True)
    args = parser.parse_args()
    prepare(
        *(
            p.resolve()
            for p in (args.config, args.geometry, args.producer_manifest, args.output, args.receipt)
        )
    )
