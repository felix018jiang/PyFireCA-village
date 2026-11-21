"""Validate the frozen equal-count and area-matched heritage position controls."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pyfireca.planning.heritage import content_hash

ROOT = Path(__file__).resolve().parents[1]


def read(path):
    return json.loads(Path(path).read_text())


def load_reporter():
    path = Path(__file__).with_name("summarize_heritage_random_sources.py")
    spec = importlib.util.spec_from_file_location("position_random_reporter", path)
    reporter = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(reporter)
    return reporter


def summarize(manifest_path):
    manifest_path = Path(manifest_path).resolve()
    manifest = read(manifest_path)
    reference = read(manifest_path.parent / manifest["reference_result"])
    assert content_hash(reference) == manifest["reference_result_sha256"]
    selection_protocol = read(manifest_path.parent / manifest["selection_reference_protocol"])
    assert content_hash(selection_protocol) == manifest["selection_reference_protocol_sha256"]
    reporter = load_reporter()
    reports, runs, realized_geometries = [], [], set()
    inventory = None
    config_plan_ids = None
    for cohort in manifest["cohorts"]:
        config_path = manifest_path.parent / cohort["config"]
        config = read(config_path)
        assert content_hash(config) == cohort["config_sha256"]
        assert config["seed"] == cohort["seed"]
        assert config["selection_result_sha256"] == manifest["reference_result_sha256"]
        assert config["target_source_ids"] == manifest["source_ids"]
        root = (manifest_path.parent / cohort["output"]).resolve()
        experiment = read(root / "experiment.json")
        assert experiment["config"] == config
        report = reporter.summarize(root)
        reports.append({"cohort": cohort["label"], "validation": report["validation"]})
        runs.extend({**row, "cohort": cohort["label"]} for row in report["runs"])
        this_inventory = read(root / "inventory.json")
        if inventory is None:
            inventory = this_inventory
            config_plan_ids = [p["plan_id"] for p in config["plans"]]
        else:
            assert this_inventory == inventory
            assert [p["plan_id"] for p in config["plans"]] == config_plan_ids
        bank = read(root / "realized-sources.json")
        baseline = bank["baseline"]
        for plan_id in config_plan_ids:
            assert bank[plan_id] == baseline
        fired = [r for r in baseline if r["status"] == "fire"]
        assert {r["source_id"] for r in fired} == set(manifest["source_ids"])
        for row in fired:
            sha = content_hash(row["footprint"]["geometry"])
            assert sha not in realized_geometries
            realized_geometries.add(sha)
    followup = None
    followup_path = manifest_path.with_name("batuan-position-controls-followup-20261024.json")
    if followup_path.exists():
        declaration = read(followup_path)
        assert declaration["parent_manifest_sha256"] == content_hash(manifest)
        trigger_hashes = {
            row["evaluation_sha256"]
            for row in runs
            if row["plan_id"] == "baseline" and row["source_id"] == "B0062"
        }
        assert trigger_hashes == set(declaration["trigger_baseline_evaluation_sha256"])
        assert all(
            row["protection_status"] == "passed"
            for row in runs
            if row["plan_id"] == "baseline" and row["source_id"] == "B0062"
        )
        config_path = manifest_path.parent / declaration["config"]
        config = read(config_path)
        assert content_hash(config) == declaration["config_sha256"]
        source_protocol = read(manifest_path.parent / declaration["source_protocol"])
        assert content_hash(source_protocol) == declaration["source_protocol_sha256"]
        root = (manifest_path.parent / declaration["output"]).resolve()
        report = reporter.summarize(root)
        assert len(report["runs"]) == declaration["new_simulations"]
        source_reference = (manifest_path.parent / declaration["source_reference"]).resolve()
        old_bank = read(source_reference / "realized-sources.json")["baseline"]
        old_source = next(r for r in old_bank if r["draw_id"] == declaration["source_draw_id"])
        new_bank = read(root / "realized-sources.json")
        for rows in new_bank.values():
            assert rows[0]["source_id"] == "B0062" and not rows[0]["source_replaced"]
            assert content_hash(rows[0]["footprint"]["geometry"]) == content_hash(
                old_source["footprint"]["geometry"]
            )
        runs.extend({**row, "cohort": "known-B0062"} for row in report["runs"])
        reports.append({"cohort": "known-B0062", "validation": report["validation"]})
        followup = {
            "new_simulations": declaration["new_simulations"],
            "source_id": "B0062",
            "source_draw_id": declaration["source_draw_id"],
            "original_polygon_reproduced": True,
            "is_independent_confirmation": False,
        }
    expected_runs = manifest["planned_new_simulations"] + (
        followup["new_simulations"] if followup else 0
    )
    assert len(runs) == expected_runs
    assert len(realized_geometries) == manifest["independent_fire_draws"]
    assert all(not row["source_replaced"] for row in runs)

    source_reentry = None
    source_reentry_path = manifest_path.with_name(
        "batuan-position-controls-B0061-followup-20261024.json"
    )
    if source_reentry_path.exists():
        declaration = read(source_reentry_path)
        assert declaration["parent_manifest_sha256"] == content_hash(manifest)
        config_path = manifest_path.parent / declaration["config"]
        config = read(config_path)
        assert content_hash(config) == declaration["config_sha256"]
        source_protocol = read(manifest_path.parent / declaration["source_protocol"])
        assert content_hash(source_protocol) == declaration["source_protocol_sha256"]
        root = (manifest_path.parent / declaration["output"]).resolve()
        report = reporter.summarize(root)
        assert len(report["runs"]) == declaration["new_simulations"]
        source_reference = (manifest_path.parent / declaration["source_reference"]).resolve()
        old_bank = read(source_reference / "realized-sources.json")["baseline"]
        old_source = next(r for r in old_bank if r["draw_id"] == declaration["source_draw_id"])
        assert old_source["source_id"] == "B0061"
        old_polygon_sha256 = content_hash(old_source["footprint"]["geometry"])
        new_bank = read(root / "realized-sources.json")
        replaced_polygon_sha256 = None
        for plan in config["plans"]:
            row = new_bank[plan["plan_id"]][0]
            removes_source = "B0061" in plan["demolished_ids"]
            assert row["source_replaced"] == removes_source
            assert row["source_id"] == ("B0063" if removes_source else "B0061")
            polygon_sha256 = content_hash(row["footprint"]["geometry"])
            if removes_source:
                if replaced_polygon_sha256 is None:
                    replaced_polygon_sha256 = polygon_sha256
                assert polygon_sha256 == replaced_polygon_sha256
            else:
                assert polygon_sha256 == old_polygon_sha256
        runs.extend({**row, "cohort": "known-B0061"} for row in report["runs"])
        reports.append({"cohort": "known-B0061", "validation": report["validation"]})
        source_reentry = {
            "new_simulations": declaration["new_simulations"],
            "source_draw_id": declaration["source_draw_id"],
            "retained_source_id": "B0061",
            "replacement_source_id": "B0063",
            "original_B0061_polygon_reproduced": True,
            "replacement_polygon_equal_across_relevant_arms": True,
            "is_independent_confirmation": False,
            "is_same_source_comparison": False,
        }
        expected_runs += declaration["new_simulations"]
        assert len(runs) == expected_runs

    geometry = {
        b["building_id"]: unary_union([Polygon(p["exterior"], p["holes"]) for p in b["polygons"]])
        for b in inventory["buildings"]
    }
    selected = manifest["plans"][0]
    selected_area = sum(geometry[i].area for i in selected["demolished_ids"])
    bridge_ids = selection_protocol["strata"]["near-B0120"]
    drum_ids = selection_protocol["strata"]["near-B0073"]
    expected_global = min(
        (
            (bridge, *drums)
            for bridge in bridge_ids
            for drums in itertools.combinations(drum_ids, 2)
            if set((bridge, *drums)) != set(selected["demolished_ids"])
        ),
        key=lambda ids: (abs(sum(geometry[i].area for i in ids) - selected_area), sorted(ids)),
    )
    expected_drum = min(
        itertools.combinations([i for i in drum_ids if i not in {"B0071", "B0076"}], 2),
        key=lambda ids: (
            abs(geometry["B0061"].area + sum(geometry[i].area for i in ids) - selected_area),
            sorted(ids),
        ),
    )
    assert set(manifest["plans"][1]["demolished_ids"]) == {"B0063", "B0071", "B0076"}
    assert set(manifest["plans"][2]["demolished_ids"]) == {"B0061", *expected_drum}
    assert set(manifest["plans"][3]["demolished_ids"]) == set(expected_global)

    core_ids = [t["building_id"] for t in inventory["targets"]]
    summaries = []
    draw_status = {}
    for plan_id in config_plan_ids:
        plan_rows = [r for r in runs if r["plan_id"] == plan_id]
        draws = []
        for cohort in sorted({r["cohort"] for r in plan_rows}):
            for draw_id in sorted({r["draw_id"] for r in plan_rows if r["cohort"] == cohort}):
                rows = [r for r in plan_rows if r["cohort"] == cohort and r["draw_id"] == draw_id]
                assert {r["scale_m"] for r in rows} == {1.0, 0.5}
                ignited = {
                    core: any(
                        outcome["ignited"]
                        for row in rows
                        for outcome in row["core_outcomes"]
                        if outcome["building_id"] == core
                    )
                    for core in core_ids
                }
                status = "failed" if any(ignited.values()) else "passed"
                source_id = rows[0]["source_id"]
                key = f"{cohort}/{draw_id}"
                draw_status[plan_id, key] = status
                draws.append(
                    {
                        "draw_key": key,
                        "source_id": source_id,
                        "status": status,
                        "core_ignited": ignited,
                        "scale_results": [
                            {
                                "scale_m": row["scale_m"],
                                "first_ignition_s": {
                                    o["building_id"]: o["first_ignition_s"]
                                    for o in row["core_outcomes"]
                                },
                                "maximum_temperature_K": {
                                    o["building_id"]: o["maximum_valid_surface_temperature_K"]
                                    for o in row["core_outcomes"]
                                },
                            }
                            for row in sorted(rows, key=lambda r: -r["scale_m"])
                        ],
                    }
                )
        ledger = plan_rows[0]["intervention"]
        summaries.append(
            {
                "plan_id": plan_id,
                "draws": len(draws),
                "joint_failures": sum(d["status"] == "failed" for d in draws),
                "core_failures": {
                    core: sum(d["core_ignited"][core] for d in draws) for core in core_ids
                },
                "source_failures": {
                    source: sum(d["status"] == "failed" and d["source_id"] == source for d in draws)
                    for source in sorted({d["source_id"] for d in draws})
                },
                "intervention": ledger,
                "draw_results": draws,
            }
        )
    selected_id = selected["plan_id"]
    advantages = []
    keys = sorted({key for plan, key in draw_status if plan == selected_id})
    for alternative in (p["plan_id"] for p in manifest["plans"][1:]):
        advantages.append(
            {
                "alternative_plan_id": alternative,
                "selected_pass_alternative_fail": sum(
                    draw_status[selected_id, key] == "passed"
                    and draw_status[alternative, key] == "failed"
                    for key in keys
                ),
                "alternative_pass_selected_fail": sum(
                    draw_status[alternative, key] == "passed"
                    and draw_status[selected_id, key] == "failed"
                    for key in keys
                ),
                "both_pass": sum(
                    draw_status[selected_id, key] == draw_status[alternative, key] == "passed"
                    for key in keys
                ),
                "both_fail": sum(
                    draw_status[selected_id, key] == draw_status[alternative, key] == "failed"
                    for key in keys
                ),
            }
        )
    for summary in summaries:
        ledger = summary["intervention"]
        ids = ledger["demolished_ids"]
        union = unary_union([geometry[i] for i in ids]) if ids else Polygon()
        remaining = unary_union([g for i, g in geometry.items() if i not in ids])
        expected = {
            "demolished_footprint_area_m2": sum(geometry[i].area for i in ids),
            "cleared_roof_union_area_m2": union.difference(remaining).area,
            "retained_shared_roof_area_m2": union.intersection(remaining).area,
        }
        for name, value in expected.items():
            np.testing.assert_allclose(ledger[name], value, rtol=1e-7, atol=1e-5)
    return {
        "schema_version": "heritage-position-controls-results-1",
        "complete": True,
        "manifest_sha256": content_hash(manifest),
        "new_simulations": len(runs),
        "independent_fire_draws": len(realized_geometries),
        "known_adverse_bridge_stress": followup,
        "known_B0061_source_reentry": source_reentry,
        "plans": summaries,
        "selected_position_advantage": advantages,
        "validation": {
            "all_runs_complete_valid": True,
            "same_source_polygon_budget_across_primary_and_B0062_stress": True,
            "known_B0061_source_reentry_verified": source_reentry is not None,
            "selection_rules_recomputed": True,
            "all_area_ledgers_match_vector_geometry": True,
            "producer_snapshots_energy_and_core_identities_verified": True,
            "cohorts": reports,
        },
        "whole_village_failure_estimate": None,
        "optimization": False,
        "interpretation": manifest["claim"],
        "runs": runs,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.manifest)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {
                "complete": result["complete"],
                "new_simulations": result["new_simulations"],
                "plans": [
                    {
                        "plan_id": p["plan_id"],
                        "joint_failures": p["joint_failures"],
                        "core_failures": p["core_failures"],
                    }
                    for p in result["plans"]
                ],
                "selected_position_advantage": result["selected_position_advantage"],
            },
            ensure_ascii=False,
        )
    )
