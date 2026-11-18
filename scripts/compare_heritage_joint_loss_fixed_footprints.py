"""Compare the Batuan placements on every common retained-source footprint."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import summarize_heritage_joint_pareto as native

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def compare(root):
    folder = root / "examples/research/heritage/area-20261110"
    runs = root / "runs/research"
    arms = {
        "joint": (
            "heritage-joint-loss-native-batuan-20261111",
            "batuan-joint-loss-native-20261111",
        ),
        "economic": (
            "heritage-joint-loss-matched-reference-batuan-20261111",
            "batuan-joint-loss-matched-reference-20261111",
        ),
    }
    controls = (
        "source_id",
        "source_footprint_sha256",
        "source_budget",
        "scale_m",
        "common_layout_sha256",
        "frozen_integrated_node_mask_sha256",
        "fire_id",
    )
    configs, exps, reports = {}, {}, {}
    with TemporaryDirectory() as tmp:
        for key, (run_name, cfg) in arms.items():
            run = runs / run_name
            conf = folder / (cfg + ".json")
            configs[key] = read_json(conf)
            exps[key] = read_json(run / "experiment.json")
            reports[key] = native.summarize(run, conf, Path(tmp) / (key + ".json"))
    joint, base = exps["joint"], exps["economic"]
    common = (
        "reference_master_experiment_sha256",
        "terminal_horizon_cap_s",
        "checkpoints_s",
        "source_fires",
        "source_policy",
        "core_overlap_policy",
        "quadrature_spacing_m",
        "max_dt_s",
        "roof_scales_m",
        "required_solver_source_sha256",
        "study_path",
    )
    if (
        any(configs["joint"][k] != configs["economic"][k] for k in common)
        or joint["source_sha256"] != base["source_sha256"]
        or joint["problem"]["fires"] != base["problem"]["fires"]
    ):
        raise ValueError("the experiments did not use the same registered mesh and equations")
    cert_path = (
        folder / "batuan-joint-loss-matched-reference-20261111.finite-source-certificate.json"
    )
    if configs["economic"]["additional_frozen_inputs_sha256"][str(cert_path)] != file_sha256(
        cert_path
    ):
        raise ValueError("source match selection changed")
    by_fire = {}
    for row in read_json(cert_path)["rows"]:
        by_fire.setdefault(row["fire_id"], []).append(row)
    matched = set()
    for name, rows in by_fire.items():
        if len(rows) != 2:
            raise ValueError("both registered interventions required")
        if len({(r["actual_source_id"], r["source_footprint_sha256"]) for r in rows}) == 1:
            matched.update(f"{name}-h{s:g}" for s in configs["joint"]["roof_scales_m"])
    planned = {r["scenario_id"] for r in configs["economic"]["execution_case_selection"]}
    if matched != planned or len(matched) != 4 or len(by_fire) != 8:
        raise ValueError("not all and only matched registered families were selected")
    pairs = []
    for name in sorted(matched):
        outcomes, evidence = {}, {}
        for key in arms:
            plan_id = (
                configs["joint"]["plans"][0]["plan_id"]
                if key == "joint"
                else configs["economic"]["execution_case_selection"][0]["plan_id"]
            )
            row = next(
                r
                for r in reports[key]["native_checks"]
                if r["scenario_id"] == name and r["plan_id"] == plan_id
            )
            if (
                row["status"] != "passed"
                or not row["terminal_complete"]
                or row["invalid_reasons"]
                or row["core_ignition_observed"] is not False
                or not row["certificate_observation_bound"]
            ):
                raise ValueError("paired strict terminal results pending or invalid")
            manifest = Path(row["manifest"])
            check = manifest.parent / "check.json"
            if (
                file_sha256(manifest) != row["manifest_sha256"]
                or file_sha256(check) != read_json(manifest)["files_sha256"][check.name]
            ):
                raise ValueError("source or state changed")
            evidence[key] = read_json(check)["evidence"]
            m = row["metrics"]
            outcomes[key] = dict(
                C_m2=m["C_construction_action_area_m2"],
                A_m2=m["burned_out_physical_covered_area_m2"],
                N=m["N_burned_out_annotation_attribution_count"],
                nonroof_A_m2=m["ignited_nonroof_covered_area_m2"],
                manifest_sha256=row["manifest_sha256"],
            )
        if any(evidence["joint"][k] != evidence["economic"][k] for k in controls):
            raise ValueError("source, footprint, starting fuel, mesh or integration mask differ")
        pairs.append(
            dict(
                case_sha256=content_hash(name),
                support_scale_m=evidence["joint"]["scale_m"],
                **outcomes,
            )
        )
    return dict(
        sampled_family_count=8,
        same_source_and_geometry_family_count=2,
        registered_scales=2,
        primary_source_draws_added=0,
        cost_loss_pairs=pairs,
        paired_fields=controls,
        verified_run_problem_sha256={k: e["problem_sha256"] for k, e in exps.items()},
        scope="all comparable registered families; previously disclosed, adaptive design",
        worst_among_four={
            k: dict(
                A_m2=max(p[k]["A_m2"] for p in pairs),
                N_upper=max(p[k]["N"]["upper"] for p in pairs),
            )
            for k in arms
        },
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = compare(Path(__file__).resolve().parents[1])
    result["receipt_sha256"] = content_hash(result)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
