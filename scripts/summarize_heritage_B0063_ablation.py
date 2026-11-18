"""Audit targeted bridge coverage and the three declared single-building addbacks."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from pyfireca.planning.heritage import content_hash

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text())


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(filename))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def summarize(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = read(manifest_path)
    reference = read(ROOT / "docs/results/heritage-triple-validation-20261024.json")
    assert content_hash(reference) == manifest["reference_result_sha256"]
    random_reporter = module("coverage_reporter", "summarize_heritage_random_sources.py")
    manual_reporter = module("addback_reporter", "summarize_heritage_bridge_manual.py")
    triple_id = manifest["fixed_candidate"]["plan_id"]
    old_manifest_path = manifest_path.with_name("batuan-triple-validation-20261024.json")
    old_manifest = read(old_manifest_path)
    old_roots = [
        (old_manifest_path.parent / p).resolve() for p in old_manifest["development_dirs"]
    ] + [ROOT / f"runs/research/heritage-triple-validation-20261024-{t}" for t in ("c", "d")]
    old_geometries = {
        content_hash(r["footprint"]["geometry"])
        for root in old_roots
        for rows in read(root / "realized-sources.json").values()
        for r in rows
        if r["footprint"] is not None
    }
    targeted_rows, targeted_draws, geometries, audits = [], [], set(), []
    for declaration in manifest["targeted_cohorts"]:
        root = ROOT / declaration["output"]
        experiment = read(root / "experiment.json")
        config = experiment["config"]
        assert config == read(manifest_path.parent / declaration["config"])
        assert content_hash(config) == declaration["config_sha256"]
        assert config["seed"] == declaration["seed"]
        assert config["selection_result_sha256"] == manifest["reference_result_sha256"]
        assert config["target_source_ids"] == ["B0063"]
        original = read(old_roots[-1] / "experiment.json")
        for key in ("parent_case_sha256", "inventory_sha256", "transport"):
            assert experiment[key] == original[key]
        for key in ("roof_scales_m", "quadrature_spacing_m", "horizon_s", "ignition_area_m2"):
            assert config[key] == original["config"][key]
        realized = read(root / "realized-sources.json")
        assert realized["baseline"] == realized[triple_id]
        for row in realized["baseline"]:
            assert row["source_id"] == "B0063"
            sha = content_hash(row["footprint"]["geometry"])
            assert sha not in old_geometries | geometries
            geometries.add(sha)
        result = random_reporter.summarize(root)
        targeted_rows.extend({**r, "cohort": declaration["label"]} for r in result["runs"])
        targeted_draws.extend(
            {**d, "cohort": declaration["label"], "plan_id": plan["plan_id"]}
            for plan in result["plans"]
            for d in plan["draws"]
        )
        audits.append({"cohort": declaration["label"], "validation": result["validation"]})
    assert len(targeted_rows) == manifest["targeted_new_runs"]
    assert len(geometries) == manifest["B0063_draws"]
    targets = [t["building_id"] for t in read(old_roots[-1] / "inventory.json")["targets"]]
    coverage = []
    for plan_id in ("baseline", triple_id):
        draws = [d for d in targeted_draws if d["plan_id"] == plan_id]
        assert len(draws) == manifest["B0063_draws"]
        coverage.append(
            {
                "plan_id": plan_id,
                "draws": len(draws),
                "joint_failures": sum(d["status"] == "failed" for d in draws),
                "core_failures": {
                    core: sum(d["core_ignited"][core] for d in draws) for core in targets
                },
                "draw_results": draws,
            }
        )
    addbacks, manual_rows = [], []
    for declaration in manifest["screen_configs"]:
        root = ROOT / declaration["output"]
        config = read(root / "experiment.json")["config"]
        assert content_hash(config) == declaration["config_sha256"]
        assert config == read(manifest_path.parent / declaration["config"])
        source_root = (manifest_path.parent / config["reference_dir"]).resolve()
        result = manual_reporter.summarize([root], source_root)
        for row in result["runs"]:
            run_id = f"{row['draw_id']}-h{row['scale_m']}".replace(".", "p")
            full = read(source_root / run_id / triple_id / "evaluation.json")
            assert row["comparison_evaluation_sha256"] == full["evaluation_sha256"]
            assert row["comparison_core_outcomes"] == full["metrics"]["core_outcomes"]
            assert row["same_source_as_comparison"]
        for plan in result["plans"]:
            failed = any(plan["core_failure_counts"].values())
            addbacks.append(
                {
                    **plan,
                    "decision": "rejected_by_counterexample"
                    if failed
                    else "requires_full_followup",
                    "cohort": declaration["cohort"],
                }
            )
        manual_rows.extend({**r, "cohort": declaration["cohort"]} for r in result["runs"])
    assert len(manual_rows) == manifest["screen_new_runs"]
    regression = None
    followup_path = manifest_path.with_name("batuan-addback-followup-20261024.json")
    if followup_path.exists():
        declaration = read(followup_path)
        assert declaration["parent_manifest_sha256"] == content_hash(manifest)
        screen = read(ROOT / "runs/research/heritage-ablation-screen-20261024-d/summary.json")
        assert content_hash(screen) == declaration["trigger_screen_result_sha256"]
        assert screen["complete"] and all(
            r["protection_status"] == "passed" for r in screen["runs"]
        )
        root = ROOT / declaration["output"]
        experiment = read(root / "experiment.json")
        assert content_hash(experiment["config"]) == declaration["config_sha256"]
        assert experiment["config"] == read(manifest_path.parent / declaration["config"])
        for key in ("parent_case_sha256", "inventory_sha256", "transport"):
            assert experiment[key] == original[key]
        for key in ("roof_scales_m", "quadrature_spacing_m", "horizon_s", "ignition_area_m2"):
            assert experiment["config"][key] == original["config"][key]
        bank = read(root / "realized-sources.json")
        old_bank = read(ROOT / declaration["source_reference"] / "realized-sources.json")
        old_source = next(
            r for r in old_bank["baseline"] if r["draw_id"] == declaration["source_draw_id"]
        )
        assert bank["baseline"][0]["source_id"] == "B0071"
        assert content_hash(bank["baseline"][0]["footprint"]["geometry"]) == content_hash(
            old_source["footprint"]["geometry"]
        )
        assert bank["baseline"] == bank["restore-B0071"]
        checked = random_reporter.summarize(root)
        assert len(checked["runs"]) == declaration["new_runs"]
        regression = {
            "new_simulations": len(checked["runs"]),
            "plans": checked["plans"],
            "runs": checked["runs"],
            "validation": checked["validation"],
            "original_B0071_polygon_reproduced": True,
            "is_independent_confirmation": False,
        }
        reduced = next(p for p in checked["plans"] if p["plan_id"] == "restore-B0071")
        failed = any(d["status"] == "failed" for d in reduced["draws"])
        if failed:
            decision = next(p for p in addbacks if p["plan_id"] == "restore-B0071")
            decision["decision"] = "rejected_by_known_failure_regression"
            decision["regression_draws"] = reduced["draws"]
        triple_regression = next(p for p in checked["plans"] if p["plan_id"] == triple_id)
        regression["frozen_triple_passed"] = all(
            d["status"] == "passed" for d in triple_regression["draws"]
        )
    followup = [p["plan_id"] for p in addbacks if p["decision"] == "requires_full_followup"]
    return {
        "schema_version": "heritage-B0063-and-addback-results-1",
        "initial_matrix_complete": True,
        "complete": not followup,
        "manifest_sha256": content_hash(manifest),
        "new_simulations": len(targeted_rows)
        + len(manual_rows)
        + (regression["new_simulations"] if regression else 0),
        "new_unique_B0063_polygons": len(geometries),
        "B0063_coverage": coverage,
        "frozen_triple_B0063_passed": coverage[-1]["joint_failures"] == 0,
        "single_addbacks": addbacks,
        "requires_followup": followup,
        "restore71_known_failure_regression": regression,
        "targeted_audits": audits,
        "targeted_runs": targeted_rows,
        "addback_runs": manual_rows,
        "interpretation": "targeted_B0063_coverage_and_local_addback_screen;not_global_minimum",
        "whole_village_failure_estimate": None,
        "optimization": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.manifest)
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    print(
        json.dumps(
            {
                k: result[k]
                for k in ("complete", "requires_followup", "B0063_coverage", "single_addbacks")
            },
            ensure_ascii=False,
        )
    )
