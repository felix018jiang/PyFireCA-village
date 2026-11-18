"""Compare a complete native run against the precomputed per-family containment bounds."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory

import summarize_heritage_joint_pareto as native

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json

ROOT = Path(__file__).resolve().parents[1]


def verified_json(path):
    data = read_json(path)
    if data["receipt_sha256"] != content_hash(
        {k: v for k, v in data.items() if k != "receipt_sha256"}
    ):
        raise ValueError(f"certificate digest changed: {path}")
    return data


def audit(village, campaign_filter=None):
    results = ROOT / "docs/results"
    runs = ROOT / "runs/research"
    configs = ROOT / "examples/research/heritage/area-20261110"
    cost_path = results / f"heritage-total-protection-and-loss-{village}-20261111.json"
    opt_path = results / f"heritage-total-protection-and-loss-{village}-independent-20261111.json"
    cost, proof = verified_json(cost_path), verified_json(opt_path)
    if proof["joint_candidate_sha256"] != file_sha256(cost_path):
        raise ValueError("independent optimality and candidate disagree")
    geography = verified_json(
        results / f"heritage-registered-roof-compartment-{village}-20261111.json"
    )
    campaigns = {"design": runs / f"heritage-thermal-and-geometric-pareto-{village}-20261111"}
    if village == "batuan":
        campaigns["previously_disclosed_confirmation"] = (
            runs / "heritage-batuan-independent-D9-G0-20261111"
        )
    bound_rows = {
        (x["campaign"], x["declared_family_ordinal"]): x
        for x in cost["registered_family_loss_bounds"]
    }
    bindings = {}
    for campaign, path in campaigns.items():
        original = path / "experiment.json"
        if file_sha256(original) != geography["frozen_input_campaign_files_sha256"][campaign]:
            raise ValueError("registered family catalogue changed")
        for ordinal, (name, fire) in enumerate(read_json(original)["problem"]["fires"].items(), 1):
            if name in bindings:
                raise ValueError("duplicated logical family across catalogues")
            protocol = fire["protocol"]
            digest = content_hash({k: v for k, v in protocol.items() if k != "protocol_sha256"})
            if digest != protocol["protocol_sha256"]:
                raise ValueError("original cohort protocol checksum mismatch")
            bindings[name] = bound_rows[campaign, ordinal], digest

    config = configs / f"{village}-joint-loss-native-20261111.json"
    run = runs / f"heritage-joint-loss-native-{village}-20261111"
    expected = {
        r["fire_id"]: r
        for r in read_json(
            configs / f"{village}-joint-loss-native-20261111.finite-source-certificate.json"
        )["rows"]
    }
    if set(bindings) != set(expected) or len(bindings) != cost["fixed_25m2_source_budget_count"]:
        raise ValueError("different cohort denominators")
    action = next(
        r for r in cost["optimized_total_construction"] if r["core_energy_ceiling"] == 0.99
    )
    with TemporaryDirectory() as tmp:
        report = native.summarize(run, config, Path(tmp) / "native.json", temporal_mode="strict")
    plans = list(report["archive"]["fully_evaluated_feasible_plans"])
    if campaign_filter is not None:
        plans.extend(report["archive"]["unresolved_plans"])
        plans.extend(report["archive"]["failed_plans"])
    if len(plans) != 1 or plans[0]["plan_id"] != "joint-core-and-village-loss":
        raise ValueError("full strict closure and heritage protection are pending")
    plan = plans[0]
    if (
        sorted(plan["demolished_ids"]) != sorted(action["demolished_ids"])
        or sorted(plan["cleared_physical_cell_ids"]) != sorted(action["cleared_physical_cell_ids"])
        or abs(plan["cleared_area_m2"] - action["C_m2"]) > 1e-5
    ):
        raise ValueError("replay measured a different physical intervention")
    rows = []
    scales = set(read_json(config)["roof_scales_m"])
    selected = {
        name
        for name, (bound, _) in bindings.items()
        if campaign_filter is None or bound["campaign"] == campaign_filter
    }
    if not selected:
        raise ValueError("requested cohort is not registered for this village")
    selected_ids = {f"{name}-h{scale:g}" for name in selected for scale in scales}
    cases = set()
    for check in report["native_checks"]:
        if check["scenario_id"] not in selected_ids:
            continue
        if "manifest" not in check:
            raise ValueError("requested strict native case has not completed")
        manifest = Path(check["manifest"])
        if file_sha256(manifest) != check["manifest_sha256"]:
            raise ValueError("manifest changed after native validation")
        trace = manifest.parent / "check.json"
        if file_sha256(trace) != read_json(manifest)["files_sha256"][trace.name]:
            raise ValueError("native evidence checksum changed")
        ev = read_json(trace)["evidence"]
        name, scale = ev["fire_id"], ev["scale_m"]
        bound, protocol = bindings[name]
        source = expected[name]
        if (
            source["actual_source_id"] != ev["source_id"]
            or source["source_footprint_sha256"] != ev["source_footprint_sha256"]
            or source["protocol_sha256"] != protocol
            or abs(ev["source_budget"]["covered_area_m2"] - 25) > 1e-6
        ):
            raise ValueError("actual footprint or cohort changed")
        metrics = check["metrics"]
        area = metrics["burned_out_physical_covered_area_m2"]
        owners = metrics["N_burned_out_annotation_attribution_count"]
        if (
            check["status"] != "passed"
            or not check["terminal_complete"]
            or check["invalid_reasons"]
            or not check["certificate_observation_bound"]
            or check["core_ignition_observed"] is not False
            or not ev["core_outcomes"]
            or any(x["ignited"] is not False for x in ev["core_outcomes"])
            or metrics["ignited_nonroof_covered_area_m2"] != 0
            or area > bound["single_roof_A_upper_m2"]
            or owners["upper"] > bound["single_roof_N_owner_upper"]
        ):
            raise ValueError(
                "native experiment violates its closure, protection or loss prediction"
            )
        cases.add((name, scale))
        rows.append(
            dict(
                campaign=bound["campaign"],
                ordinal=bound["declared_family_ordinal"],
                roof_scale_m=scale,
                certified_A_upper_m2=bound["single_roof_A_upper_m2"],
                certified_N_upper=bound["single_roof_N_owner_upper"],
                measured_A_m2=area,
                measured_N=owners,
                measured_nonroof_A_m2=metrics["ignited_nonroof_covered_area_m2"],
                maximum_core_temperature_K=max(
                    q["maximum_valid_surface_temperature_K"] for q in ev["core_outcomes"]
                ),
                all_core_incoming_fire_heat_J=sum(
                    q["absorbed_radiation_J"] + q["received_convection_J"]
                    for q in ev["core_outcomes"]
                ),
                stop_time_s=check["actual_stop_time_s"],
                manifest_sha256=check["manifest_sha256"],
            )
        )
    if len(rows) != len(cases) or cases != {(name, scale) for name in selected for scale in scales}:
        raise ValueError("native observations do not span the registered catalogue")
    return dict(
        village=village,
        C_m2=action["C_m2"],
        native_problem_sha256=report["native_comparison_problem_sha256"],
        experiment_sha256=report["experiment_sha256"],
        bound_certificate_sha256=file_sha256(cost_path),
        cost_optimality_certificate_sha256=file_sha256(opt_path),
        checked_native_cases=rows,
        registered_families=len(selected),
        total_registered_families_in_native_run=len(bindings),
        cohort=campaign_filter or "all_registered_families",
        strict_heritage_no_ignition_all=True,
        all_cases_within_their_specific_loss_upper_bounds=True,
        cases_with_any_positive_core_incoming_fire_heat=sum(
            r["all_core_incoming_fire_heat_J"] > 0 for r in rows
        ),
        maximum_measured_core_temperature_K=max(r["maximum_core_temperature_K"] for r in rows),
        worst_measured_A_m2=max(r["measured_A_m2"] for r in rows),
        worst_measured_N=dict(
            lower=max(r["measured_N"]["lower"] for r in rows),
            upper=max(r["measured_N"]["upper"] for r in rows),
        ),
        method_sha256=file_sha256(__file__),
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--village", choices=("batuan", "pingyan"), required=True)
    parser.add_argument("--campaign", choices=("design", "previously_disclosed_confirmation"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = audit(args.village, args.campaign)
    result["receipt_sha256"] = content_hash(result)
    with args.output.open("x") as handle:
        json.dump(result, handle, indent=2, allow_nan=False)
        handle.write("\n")
