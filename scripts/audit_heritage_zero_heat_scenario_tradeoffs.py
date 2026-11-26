"""Determine Pareto membership for worst-case and case-by-case losses on common native fires."""

import json
import math
from pathlib import Path

import numpy as np

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json


def need(p, msg):
    if not p:
        raise ValueError(msg)


def audit(root):
    runs = root / "runs/research"
    res = root / "docs/results"
    source = res / "heritage-pingyan-two-zero-heat-native-strict-paired-20261111.json"
    paired = read_json(source)
    need(
        paired["receipt_sha256"]
        == content_hash({k: v for k, v in paired.items() if k != "receipt_sha256"})
        and paired["paired_strict_naturally_closed_checks"] == 8,
        "eight strict cases on exactly the same physical inputs required",
    )
    roots = [
        runs / "heritage-pingyan-zero-core-heat-and-design-loss-20261111",
        runs / "heritage-pingyan-zero-heat-minimum-native-20261111",
    ]
    p0, p1 = [read_json(p / "experiment.json")["config"]["plans"][0] for p in roots]
    need(p0["demolished_ids"] == p1["demolished_ids"], "ordinary roof removals differ")
    restored = set(p0["cleared_physical_cell_ids"]) - set(p1["cleared_physical_cell_ids"])

    def native_table(root):
        target = {}
        for m in root.glob("evaluations/*/complete.json"):
            raw = read_json(m)
            folder = m.parent
            need(
                all(file_sha256(folder / x) == v for x, v in raw["files_sha256"].items()),
                "raw manifest changed",
            )
            ev = read_json(folder / "check.json")["evidence"]
            target[ev["fire_id"], ev["scale_m"]] = (folder, ev)
        return target

    a, b = map(native_table, roots)
    need(a.keys() == b.keys() and len(a) == 8, "unequal physical case domains")
    rows = []
    for key in a:
        (pf, ef), (pg, eg) = a[key], b[key]
        need(
            all(
                ef[f] == eg[f]
                for f in [
                    "source_id",
                    "source_footprint_sha256",
                    "source_budget",
                    "common_layout_sha256",
                    "frozen_integrated_node_mask_sha256",
                    "scale_m",
                ]
            ),
            "unmatched physical source",
        )
        with (
            np.load(pf / "final-state.npz") as sf,
            np.load(pg / "final-state.npz") as sg,
            np.load(pf / "loss-basis.npz") as cf,
            np.load(pg / "loss-basis.npz") as cg,
            np.load(pg / "layout.npz") as lay,
        ):
            old = np.isfinite(sf["burnout_time_s"].ravel())
            new = np.isfinite(sg["burnout_time_s"].ravel())
            ca = cf["post_intervention_covered_area_m2"].ravel()
            cb = cg["post_intervention_covered_area_m2"].ravel()
            cell = lay["physical_cell_id"].ravel()
            offsets = lay["owner_offsets"]
            add = np.flatnonzero(new & ~old)
            drop = np.flatnonzero(old & ~new)
            verify_extra = all(
                int(cell[i]) in restored
                and ca[i] == 0
                and cb[i] > 0
                and offsets[i] == offsets[i + 1]
                for i in add
            )
            a0 = math.fsum(ca[old])
            a1 = math.fsum(cb[new])
            area_add = math.fsum(cb[add])
            area_lost = math.fsum(ca[drop])
            need(
                math.isclose(a1 - a0, area_add - area_lost, abs_tol=1e-7),
                "burned area change unexplained by changed support",
            )
        count0 = read_json(pf / "fire-loss.json")["physical_remaining_roof_annotation_counts"][
            "burned_out_annotation_attribution_count"
        ]
        count1 = read_json(pg / "fire-loss.json")["physical_remaining_roof_annotation_counts"][
            "burned_out_annotation_attribution_count"
        ]
        rows.append(
            dict(
                scale=key[1],
                a0=a0,
                a1=a1,
                n0=count0["upper"],
                n1=count1["upper"],
                all_N_equal=count0 == count1,
                all_added_ground=verify_extra,
                added=area_add,
                lost=area_lost,
            )
        )
    cost = (paired["original_cost_m2"], paired["enumerated_minimum_cost_m2"])
    need(cost[0] > cost[1], "new placement no longer cheaper")
    worst = lambda arm: (max(x["a" + arm] for x in rows), max(x["n" + arm] for x in rows))
    w0, w1 = worst("0"), worst("1")
    minimax = w1[0] <= w0[0] + 1e-7 and w1[1] <= w0[1]
    each = all(x["a1"] <= x["a0"] + 1e-7 and x["n1"] <= x["n0"] for x in rows)
    per_scale = []
    for h in sorted({x["scale"] for x in rows}, reverse=True):
        r = [x for x in rows if x["scale"] == h]
        delta = [x["a1"] - x["a0"] for x in r]
        per_scale.append(
            dict(
                local_roof_mesh_m=h,
                distinct_original_design_families=len(r),
                equal_area_families=sum(abs(d) <= 1e-7 for d in delta),
                minimum_plan_increased_area_families=sum(d > 1e-7 for d in delta),
                minimum_plan_decreased_area_families=sum(d < -1e-7 for d in delta),
                largest_area_increase_m2=max(delta),
                all_counts_lower_and_upper_unchanged=all(x["all_N_equal"] for x in r),
                uniform_descriptive_mean_burned_m2_original=math.fsum(x["a0"] for x in r) / len(r),
                uniform_descriptive_mean_burned_m2_minimum=math.fsum(x["a1"] for x in r) / len(r),
            )
        )
    result = dict(
        schema_version="two-zero-core-placements-casewise-and-worst-case-pareto-1",
        source_paired_file_sha256=file_sha256(source),
        old_construction_m2=cost[0],
        new_construction_m2=cost[1],
        finite_comparison_within_these_two_placements_only=True,
        ranked_by_C_worst_A_worst_Nupper={
            "old_nondominated": not minimax,
            "new_nondominated": True,
            "old_worst_A_and_Nupper": list(w0),
            "new_worst_A_and_Nupper": list(w1),
        },
        ranked_by_C_and_separate_A_Nupper_for_every_tested_source_and_scale={
            "old_nondominated": not each,
            "new_nondominated": True,
        },
        all_additional_burned_material_was_restored_eligible_ground=all(
            x["all_added_ground"] for x in rows
        ),
        all_burned_original_building_count_bounds_unchanged=all(x["all_N_equal"] for x in rows),
        by_local_mesh_scale=per_scale,
        interpretation_of_means="Each of the four prespecified design sources gets equal weight within a mesh. This is a descriptive finite-design average and makes no assumption about village ignition frequency.",
        method_sha256=file_sha256(__file__),
    )
    result["receipt_sha256"] = content_hash(result)
    with (
        res / "heritage-pingyan-two-zero-heat-casewise-and-worst-loss-tradeoffs-20261111.json"
    ).open("x") as f:
        json.dump(result, f, indent=2, allow_nan=False)
        f.write("\n")
    print(
        "compared worst-case and case-by-case dominance",
        minimax,
        each,
        [(r["local_roof_mesh_m"], r["largest_area_increase_m2"]) for r in per_scale],
        flush=True,
    )


if __name__ == "__main__":
    audit(Path(__file__).resolve().parents[1])
