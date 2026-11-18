"""Check the small prescribed gulou matrix and its one permitted follow-up plan."""

from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path

from pyfireca.planning.heritage import content_hash


def read(path):
    return json.loads(Path(path).read_text())


def summarize(manifest_path, roots):
    manifest_path = Path(manifest_path).resolve()
    manifest = read(manifest_path)
    spec = importlib.util.spec_from_file_location(
        "manual_reporter", Path(__file__).with_name("summarize_heritage_bridge_manual.py")
    )
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    grouped = {}
    for root in map(Path, roots):
        config = read(root / "experiment.json")["config"]
        grouped.setdefault(config["validation_cohort"], []).append(root)
    assert set(grouped) == {r["cohort"] for r in manifest["main_cohorts"]}
    rows, followup = [], []
    for declared in manifest["main_cohorts"]:
        label = declared["cohort"]
        selected = grouped[label]
        configs = [read(root / "experiment.json")["config"] for root in selected]
        reference = (manifest_path.parent / configs[0]["reference_dir"]).resolve()
        checked = reporter.summarize(selected, reference)
        main = [c for c in configs if c.get("stage", "main") == "main"]
        assert len(main) == 1 and content_hash(main[0]) == declared["config_sha256"]
        for config in configs:
            assert config["required_demolished_ids"] == manifest["required_demolished_ids"]
            assert config["comparison_plan_id"] == manifest["comparison_plan_id"]
            assert (manifest_path.parent / config["reference_dir"]).resolve() == reference
            if config.get("stage") == "confirmation":
                followup.append(config)
        for row in checked["runs"]:
            assert set(manifest["required_demolished_ids"]) <= set(
                row["intervention"]["demolished_ids"]
            )
            run_id = f"{row['draw_id']}-h{row['scale_m']}".replace(".", "p")
            comparison = read(
                reference / run_id / manifest["comparison_plan_id"] / "evaluation.json"
            )
            assert row["comparison_evaluation_sha256"] == comparison["evaluation_sha256"]
            assert row["comparison_core_outcomes"] == comparison["metrics"]["core_outcomes"]
            assert row["same_source_as_comparison"] == (
                comparison["metrics"]["actual_source_ids"] == [row["source_id"]]
            )
            rows.append({**row, "cohort": label})
    plans = []
    for declared_plan in manifest["plans"]:
        plan_id = declared_plan["plan_id"]
        selected = [r for r in rows if r["plan_id"] == plan_id]
        expected_ids = set(declared_plan["demolished_ids"])
        assert all(set(r["intervention"]["demolished_ids"]) == expected_ids for r in selected)
        main_draws = []
        for declaration in manifest["main_cohorts"]:
            config = read(manifest_path.parent / declaration["file"])
            draws = next(p["draw_ids"] for p in config["plans"] if p["plan_id"] == plan_id)
            for draw_id in draws:
                pair = [
                    r
                    for r in selected
                    if r["cohort"] == declaration["cohort"] and r["draw_id"] == draw_id
                ]
                assert {r["scale_m"] for r in pair} == set(config["roof_scales_m"])
                main_draws.append(
                    {
                        "cohort": declaration["cohort"],
                        "draw_id": draw_id,
                        "source_id": pair[0]["source_id"],
                        "same_source_as_comparison": pair[0]["same_source_as_comparison"],
                        "passed_both_scales": all(r["protection_status"] == "passed" for r in pair),
                    }
                )
        plans.append(
            {
                "plan_id": plan_id,
                "additional_building_count": declared_plan["additional_building_count"],
                "additional_nominal_area_m2": declared_plan["additional_nominal_area_m2"],
                "main_draws": main_draws,
                "passes_both_failure_draws": all(r["passed_both_scales"] for r in main_draws),
                "intervention": selected[0]["intervention"],
            }
        )
    eligible = [p for p in plans if p["passes_both_failure_draws"]]
    winner = (
        min(
            eligible,
            key=lambda p: (
                p["additional_building_count"],
                p["additional_nominal_area_m2"],
                p["plan_id"],
            ),
        )
        if eligible
        else None
    )
    if followup:
        assert winner is not None and len(followup) == len(manifest["main_cohorts"])
        for config in followup:
            assert len(config["plans"]) == 1
            assert config["plans"][0]["plan_id"] == winner["plan_id"]
            assert (
                config["plans"][0]["draw_ids"]
                == manifest["confirmation_draws"][config["validation_cohort"]]
            )
        assert len(rows) == manifest["maximum_new_simulations"]
    else:
        assert len(rows) == manifest["main_simulations"]
    winner_rows = [r for r in rows if winner and r["plan_id"] == winner["plan_id"]]
    return {
        "schema_version": "heritage-gulou-manual-results-1",
        "complete": bool(followup) or winner is None,
        "main_matrix_complete": True,
        "new_simulations": len(rows),
        "manifest_sha256": content_hash(manifest),
        "selected_plan_id": winner["plan_id"] if winner else None,
        "confirmation_complete": bool(followup),
        "selected_plan_all_tested_cores_protected": (
            all(r["protection_status"] == "passed" for r in winner_rows) if followup else None
        ),
        "plans": plans,
        "runs": rows,
        "validation": {
            "saved_baselines_sources_budgets_energy_geometry_snapshots_verified": True,
            "B0061_retained_in_all_plans": True,
        },
        "interpretation": (
            "manual_development_comparisons;not_independent_confirmation_or_global_minimum"
        ),
        "optimization": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--roots", nargs="+", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.manifest, args.roots)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                key: result[key]
                for key in ("complete", "new_simulations", "selected_plan_id", "plans")
            },
            ensure_ascii=False,
        )
    )
