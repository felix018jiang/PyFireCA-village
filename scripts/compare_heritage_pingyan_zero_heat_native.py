"""Pair every strict native outcome on identical baseline and minimum-placement geometry."""

import json
import math
from pathlib import Path

import numpy as np

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def check(condition, message):
    if not condition:
        raise ValueError(message)


def cases(run, report, endpoint):
    config = read_json(run / "experiment.json")
    manifest = []
    by = {}
    for path in run.glob("evaluations/*/complete.json"):
        m = read_json(path)
        folder = path.parent
        check(
            m["problem_sha256"] == config["problem_sha256"]
            and all(file_sha256(folder / k) == h for k, h in m["files_sha256"].items()),
            "native manifests differ",
        )
        manifest.append(file_sha256(path))
        checkrow = read_json(folder / "check.json")
        e = checkrow["evidence"]
        diag = read_json(folder / "evaluation.json")["diagnostics"]
        fl = read_json(folder / "fire-loss.json")
        with np.load(folder / "final-state.npz") as st, np.load(folder / "loss-basis.npz") as lb:
            t = float(st["time_s"])
            burn = np.isfinite(st["burnout_time_s"].ravel()) & (st["burnout_time_s"].ravel() <= t)
            A = math.fsum(lb["post_intervention_covered_area_m2"].ravel()[burn])
            ign = np.isfinite(st["ignition_time_s"].ravel())
        by[e["fire_id"], e["scale_m"]] = dict(
            m=path,
            e=e,
            A=A,
            N=fl["physical_remaining_roof_annotation_counts"][
                "burned_out_annotation_attribution_count"
            ],
            strict=checkrow["status"] == "passed"
            and e["terminal_verification"]["terminal_complete"]
            and not diag["invalid_reasons"],
            burn=np.packbits(burn),
            ignite=np.packbits(ign),
            all_left_material_m2=float(
                fl["physical"]["all"]["cumulative_burned_out_covered_area_m2"]
            ),
        )
    check(
        len(manifest) == len(by) == 8
        and report["original_manifests_combined_sha256"] == content_hash(sorted(manifest))
        and report["source_file_sha256"]["experiment"] == file_sha256(run / "experiment.json")
        and report["receipt_sha256"]
        == content_hash({k: v for k, v in report.items() if k != "receipt_sha256"}),
        "endpoint or observation archive differs",
    )
    return by


def compare(root):
    results = root / "docs/results"
    runs = root / "runs/research"
    names = [
        (
            "heritage-pingyan-zero-core-heat-and-design-loss-20261111",
            "heritage-pingyan-zero-heat-and-design-loss-native-eight-endpoints-20261111.json",
        ),
        (
            "heritage-pingyan-zero-heat-minimum-native-20261111",
            "heritage-pingyan-zero-heat-minimum-native-eight-endpoints-20261111.json",
        ),
    ]
    reports = [read_json(results / file) for _, file in names]
    tables = [
        cases(runs / run, report, results / file)
        for (run, file), report in zip(names, reports, strict=True)
    ]
    a, b = tables
    check(a.keys() == b.keys(), "unpaired scenario set")
    fields = (
        "source_id",
        "source_footprint_sha256",
        "source_budget",
        "common_layout_sha256",
        "frozen_integrated_node_mask_sha256",
        "scale_m",
    )
    nvalid = 0
    equal_damage = 0
    equal_burn_masks = 0
    equal_ignition_masks = 0
    delta = []
    original_counts = []
    minimum_counts = []
    for pair, x in a.items():
        y = b[pair]
        check(
            all(x["e"][k] == y["e"][k] for k in fields),
            "unmatched original physical event, fuel, mesh, or integrated membership",
        )
        check(
            abs(x["all_left_material_m2"] - x["A"]) < 1e-7
            and abs(y["all_left_material_m2"] - y["A"]) < 1e-7,
            "physical A differs from native loss",
        )
        if not (x["strict"] and y["strict"]):
            continue
        nvalid += 1
        equal_damage += abs(x["A"] - y["A"]) < 1e-7 and x["N"] == y["N"]
        equal_burn_masks += np.array_equal(x["burn"], y["burn"])
        equal_ignition_masks += np.array_equal(x["ignite"], y["ignite"])
        delta.append(y["A"] - x["A"])
        original_counts.append(x["N"]["upper"])
        minimum_counts.append(y["N"]["upper"])
    costs = [r["C_m2"] for r in reports]
    worst = [r["strict_terminal"] for r in reports]
    fully = nvalid == 8
    dominates = (
        fully
        and costs[1] < costs[0]
        and worst[1]["worst_A_m2"] <= worst[0]["worst_A_m2"] + 1e-7
        and worst[1]["worst_N_bounds"][1] <= worst[0]["worst_N_bounds"][1]
    )
    report = dict(
        schema_version="pingyan-paired-two-zero-heat-native-full-ensemble-1",
        original_cost_m2=costs[0],
        enumerated_minimum_cost_m2=costs[1],
        number_original_registered_design_sources=4,
        total_matched_source_budget_mesh_and_integration_tuples=8,
        paired_strict_naturally_closed_checks=nvalid,
        physical_burned_area_and_original_owner_count_same_pairs=equal_damage,
        identical_burnout_node_masks=equal_burn_masks,
        identical_ignited_node_masks=equal_ignition_masks,
        per_case_area_change_min_max_m2=[min(delta), max(delta)] if delta else None,
        observed_original_worst_Nupper_and_A=worst[0],
        observed_minimum_worst_Nupper_and_A=worst[1],
        minimum_treatment_dominates_original_over_two_tested_actions_using_C_A_Nupper=dominates,
        protected_zero_heat_proven_and_measured_on_both=all(
            r["zero_core_heat_confirmed_for_all_heritage_and_8_native_checks"] for r in reports
        ),
        zero_heat_only_baseline_worst_burned_area_lower_bound_m2=reports[0][
            "physically_matched_zero_core_only_control"
        ]["zero_core_only_burned_m2_lower_bound"],
        endpoint_file_sha256=[file_sha256(results / v) for _, v in names],
        method_sha256=file_sha256(__file__),
        new_CA_checks=0,
        global_unrestricted_CA_optimality_proven=False,
    )
    report["receipt_sha256"] = content_hash(report)
    with (results / "heritage-pingyan-two-zero-heat-native-strict-paired-20261111.json").open(
        "x"
    ) as out:
        json.dump(report, out, indent=2, allow_nan=False)
        out.write("\n")
    print(
        "native physically matched zero-core-heat designs",
        nvalid,
        "equal A/N",
        equal_damage,
        "minimum dominates",
        dominates,
        flush=True,
    )


if __name__ == "__main__":
    compare(Path(__file__).resolve().parents[1])
