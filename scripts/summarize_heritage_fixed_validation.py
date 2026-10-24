"""Audit independent cohorts for a frozen demolition candidate; never run the solver."""

from __future__ import annotations

import argparse
import importlib.util
import json
from itertools import combinations
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pyfireca.planning.heritage import content_hash


def read(path):
    return json.loads(Path(path).read_text())


def summarize(manifest_path, roots, development):
    manifest_path = Path(manifest_path)
    manifest = read(manifest_path)
    if len(roots) != len(manifest["cohorts"]):
        raise ValueError("every declared validation cohort is required")
    spec = importlib.util.spec_from_file_location(
        "random_reporter", Path(__file__).with_name("summarize_heritage_random_sources.py")
    )
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    development = Path(development)
    old = read(development / "experiment.json")
    baseline_id = old["config"]["plans"][0]["plan_id"]
    development_roots = [development] + [
        (manifest_path.parent / relative).resolve()
        for relative in manifest.get("development_dirs", [])
    ]
    old_geometries = {
        content_hash(r["footprint"]["geometry"])
        for root in development_roots
        for rows in read(root / "realized-sources.json").values()
        for r in rows
        if r["footprint"] is not None
    }
    cohorts, all_runs, draws, baseline_geometries, seeds = [], [], [], set(), set()
    actual_geometries = set()
    for declaration, root in zip(manifest["cohorts"], map(Path, roots), strict=True):
        result = reporter.summarize(root)
        experiment = read(root / "experiment.json")
        config = experiment["config"]
        assert content_hash(config) == declaration["config_sha256"]
        assert read(manifest_path.parent / declaration["file"]) == config
        assert config["plans"][-1] == manifest["fixed_plan"]
        if "control_plans" in manifest:
            assert config["plans"][:-1] == manifest["control_plans"]
        assert config["selection_result_sha256"] == manifest["development_result_sha256"]
        assert config["seed"] == declaration["seed"]
        assert config["seed"] not in seeds | {old["config"]["seed"]} | set(
            manifest.get("previous_seeds", [])
        )
        seeds.add(config["seed"])
        for key in ("parent_case_sha256", "inventory_sha256", "transport"):
            assert experiment[key] == old[key]
        for key in (
            "ignition_area_m2",
            "near_distance_m",
            "roof_scales_m",
            "quadrature_spacing_m",
            "horizon_s",
        ):
            assert config[key] == old["config"][key]
        assert config["plans"][0]["plan_id"] == baseline_id
        realized = read(root / "realized-sources.json")
        for source in realized[baseline_id]:
            geometry = content_hash(source["footprint"]["geometry"])
            assert geometry not in old_geometries | baseline_geometries
            baseline_geometries.add(geometry)
        for sources in realized.values():
            for source in sources:
                if source["footprint"] is not None:
                    geometry = content_hash(source["footprint"]["geometry"])
                    assert geometry not in old_geometries
                    actual_geometries.add(geometry)
        label = config["validation_cohort"]
        cohorts.append({"cohort": label, "root": str(root), "validation": result["validation"]})
        for plan in result["plans"]:
            draws.extend({**d, "plan_id": plan["plan_id"], "cohort": label} for d in plan["draws"])
        all_runs.extend({**r, "cohort": label} for r in result["runs"])
    assert len(all_runs) == manifest["new_simulations"]
    assert len(baseline_geometries) == manifest["draws_total"]
    protocol = experiment["source_protocol"]
    core_ids = [t["building_id"] for t in experiment["study"]["targets"]]
    inventory = read(Path(roots[0]) / "inventory.json")
    geometries = {
        b["building_id"]: unary_union([Polygon(p["exterior"], p["holes"]) for p in b["polygons"]])
        for b in inventory["buildings"]
    }
    plans = []
    for plan_config in config["plans"]:
        plan_id = plan_config["plan_id"]
        selected = [d for d in draws if d["plan_id"] == plan_id]
        runs = [r for r in all_runs if r["plan_id"] == plan_id]
        removed = set(plan_config["demolished_ids"])
        removed_geometry = unary_union([geometries[i] for i in removed])
        retained_geometry = unary_union([g for i, g in geometries.items() if i not in removed])
        expected_areas = {
            "demolished_footprint_area_m2": sum(geometries[i].area for i in removed),
            "cleared_roof_union_area_m2": removed_geometry.difference(retained_geometry).area,
            "retained_shared_roof_area_m2": removed_geometry.intersection(retained_geometry).area,
        }
        for row in runs:
            assert set(row["intervention"]["demolished_ids"]) == removed
            for name, expected in expected_areas.items():
                np.testing.assert_allclose(
                    row["intervention"][name], expected, rtol=1e-8, atol=1e-5
                )
        population = {s: len(set(ids) - removed) for s, ids in protocol["strata"].items()}
        total = sum(population.values())
        strata = []
        for stratum in manifest["strata"]:
            subset = [d for d in selected if d["stratum"] == stratum]
            assert len(subset) == manifest["draws_per_stratum_total"]
            strata.append(
                {
                    "stratum": stratum,
                    "retained_source_population": population[stratum],
                    "draws": len(subset),
                    "unique_source_buildings": sorted({d["source_id"] for d in subset}),
                    "joint_failures": sum(d["status"] == "failed" for d in subset),
                    "core_failures": {
                        core: sum(d["core_ignited"][core] for d in subset) for core in core_ids
                    },
                }
            )
        weighted = sum(
            s["retained_source_population"] / total * s["joint_failures"] / s["draws"]
            for s in strata
        )
        np.testing.assert_allclose(
            weighted,
            sum(d["weight"] for d in selected if d["status"] == "failed") / len(cohorts),
        )
        plans.append(
            {
                "plan_id": plan_id,
                "retained_source_population": total,
                "draws": len(selected),
                "demolition_areas_m2": expected_areas,
                "joint_failures": sum(d["status"] == "failed" for d in selected),
                "weighted_joint_failure_fraction": weighted,
                "weighted_core_failure_fractions": {
                    core: sum(
                        s["retained_source_population"]
                        / total
                        * s["core_failures"][core]
                        / s["draws"]
                        for s in strata
                    )
                    for core in core_ids
                },
                "strata": strata,
                "core_observations": {
                    core: {
                        "maximum_temperature_K": max(
                            o["maximum_valid_surface_temperature_K"]
                            for r in runs
                            for o in r["core_outcomes"]
                            if o["building_id"] == core
                        ),
                        "first_ignition_s": min(
                            (
                                o["first_ignition_s"]
                                for r in runs
                                for o in r["core_outcomes"]
                                if o["building_id"] == core and o["ignited"]
                            ),
                            default=None,
                        ),
                    }
                    for core in core_ids
                },
            }
        )
    paired_draws = []
    for before, after in combinations(config["plans"], 2):
        by_key = {(d["cohort"], d["draw_id"], d["plan_id"]): d for d in draws}
        for d in draws:
            if d["plan_id"] != before["plan_id"]:
                continue
            other = by_key[d["cohort"], d["draw_id"], after["plan_id"]]
            paired_draws.append(
                {
                    "cohort": d["cohort"],
                    "draw_id": d["draw_id"],
                    "stratum": d["stratum"],
                    "baseline_plan_id": before["plan_id"],
                    "treated_plan_id": after["plan_id"],
                    "same_source": d["source_id"] == other["source_id"],
                    "baseline_source_id": d["source_id"],
                    "treated_source_id": other["source_id"],
                    "baseline_core_ignited": d["core_ignited"],
                    "treated_core_ignited": other["core_ignited"],
                }
            )
    return {
        "schema_version": "heritage-fixed-candidate-validation-results-1",
        "complete": True,
        "manifest_sha256": content_hash(manifest),
        "fixed_plan": manifest["fixed_plan"],
        "new_simulations": len(all_runs),
        "draws_per_plan": manifest["draws_total"],
        "cohorts": cohorts,
        "plans": plans,
        "draws": draws,
        "paired_draws": paired_draws,
        "runs": all_runs,
        "validation": {
            "fixed_candidate_and_cohorts_verified": True,
            "physical_parameters_match_development": True,
            "new_unique_baseline_source_polygons": len(baseline_geometries),
            "new_source_polygons_including_replacements": len(actual_geometries),
            "no_source_polygon_matches_any_declared_development_arm": True,
            "complete_valid_sources_budgets_energy_and_producer_snapshots": True,
            "all_demolition_ledgers_match_vector_geometry": True,
        },
        "interpretation": (
            "independent_fixed_candidate_confirmation;conditional_one_fire_fraction;"
            "not_annual_risk_or_global_optimality"
        ),
        "optimization": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--roots", nargs="+", type=Path, required=True)
    parser.add_argument("--development", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.manifest, args.roots, args.development)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {"plans": result["plans"], "validation": result["validation"]}, ensure_ascii=False
        )
    )
