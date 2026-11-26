"""Count full-village combinations and resource bounds without launching search."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from math import ceil, comb, fsum
from pathlib import Path

import numpy as np

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.roof_thermal import resolve_roof_layout

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text())


def assess(search_root, config_path):
    root = Path(search_root)
    result, experiment = read(root / "search-result.json"), read(root / "experiment.json")
    config = experiment["config"]
    spec = importlib.util.spec_from_file_location(
        "cost_roof_runner", Path(__file__).with_name("run_heritage_roof_study.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    config_path = Path(config_path).resolve()
    if read(config_path) != config:
        raise ValueError("cost assessment config differs from the frozen search")
    bundle, transport, inventory = runner.load_study(config_path.parent / config["study_path"])
    base = resolve_roof_layout(bundle.request, inventory)
    node_bounds = []
    for scale in config["roof_scales_m"]:
        # Each refined patch fits inside one scale x scale tile. Keeping
        # the existing ground nodes gives a lower bound before source cuts.
        bound = sum(
            max(1, ceil(g.area / scale**2 - 1e-8)) if ids else 1
            for ids, g in zip(base.owners, base.geometry, strict=True)
        )
        node_bounds.append(
            {
                "scale_m": scale,
                "minimum_nodes": bound,
                "max_nodes": config["max_nodes"],
                "node_limit_definitely_exceeded": bound > config["max_nodes"],
                "all_candidate_and_core_roofs_refined": True,
            }
        )
    areas = {i: inventory.by_id[i].geometry.area for i in inventory.candidate_ids}
    initial_area = fsum(areas[i] for i in config["initial_ids"])
    cheaper_three = sum(fsum(a) < initial_area for a in itertools.combinations(areas.values(), 3))
    rows = [read(p) for p in (root / "evaluations").glob("*/check.json")]
    timing = {}
    for scale in config["roof_scales_m"]:
        times = [r["evidence"]["elapsed_s"] for r in rows if r["evidence"]["scale_m"] == scale]
        timing[str(scale)] = {
            "checks": len(times),
            "minimum_s": min(times),
            "median_s": float(np.median(times)),
            "maximum_s": max(times),
        }
    counts = {str(k): comb(len(areas), k) for k in range(4)}
    lower_layers = sum(int(counts[str(k)]) for k in range(3))
    minimum_first = timing["1.0"]["minimum_s"]
    full_suite = len(config["fires"]) * sum(
        timing[str(s)]["median_s"] for s in config["roof_scales_m"]
    )

    def illustrative_seconds(combinations):
        return {
            "one_fast_first_scale_rejection_per_plan_serial_days": combinations
            * minimum_first
            / 86400,
            "full_suite_at_small_instance_medians_serial_days": combinations * full_suite / 86400,
        }

    return {
        "schema_version": "heritage-exact-cost-1",
        "search_result_sha256": content_hash(result),
        "candidate_count": len(areas),
        "combination_counts": counts,
        "zero_to_two_combinations": lower_layers,
        "three_building_combinations_with_smaller_nominal_area": cheaper_three,
        "comparison_initial_ids": config["initial_ids"],
        "initial_nominal_area_m2": initial_area,
        "base_nodes": len(base.owners),
        "whole_village_node_lower_bounds": node_bounds,
        "exposure_radius_m": transport.exposure_radius_m,
        "measured_small_instance_timings": timing,
        "zero_to_two_illustrative_cost": illustrative_seconds(lower_layers),
        "all_potentially_better_plans_illustrative_cost": illustrative_seconds(
            lower_layers + cheaper_three
        ),
        "timing_is_forecast_or_rigorous_bound": False,
        "timing_interpretation": (
            "serial_sensitivity_calculation_using_small_instance_costs;"
            "full_village_mesh_is_larger;not_a_runtime_guarantee"
        ),
        "maximum_uncached_checks_for_all_zero_to_three_plans": sum(counts.values())
        * len(config["fires"])
        * len(config["roof_scales_m"]),
        "whole_village_search_started": False,
        "three_house_upper_bound_for_new_full_village_mesh_verified": False,
        "full_village_source_rule_issue": (
            "removing_all_three_bridge_source_buildings_exhausts_that_stratum;"
            "freeze_its_treatment_before_full_village_certification;"
            "never_count_no_fire_as_success"
        ),
        "conclusion": (
            "small_exact_search_is_certifiable;full_village_certification_requires_resource_plan"
            "_and_stronger_valid_bounds_or_large_compute_budget"
        ),
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = assess(args.root, args.config)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps(report, ensure_ascii=False))
