"""Check saved manual comparisons and report outcomes per draw, unioning roof scales."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pyfireca.planning.heritage import content_hash


def read(path):
    return json.loads(Path(path).read_text())


def case_key(row):
    return row["plan_id"], row["draw_id"], row["scale_m"]


def summarize(roots, reference):
    reference = Path(reference)
    old = read(reference / "experiment.json")
    inventory = read(reference / "inventory.json")
    baseline_id = old["config"]["plans"][0]["plan_id"]
    core_ids = {t["building_id"] for t in inventory["targets"]}
    geometry = {
        b["building_id"]: unary_union([Polygon(p["exterior"], p["holes"]) for p in b["polygons"]])
        for b in inventory["buildings"]
    }
    rows, seen, budgets, footprints, baselines = [], set(), {}, {}, set()
    for root in map(Path, roots):
        summary, experiment = read(root / "summary.json"), read(root / "experiment.json")
        if not summary["complete"] or len(summary["runs"]) != summary["planned_runs"]:
            raise ValueError(f"incomplete manual matrix: {root}")
        assert experiment["reference_experiment_sha256"] == content_hash(old)
        assert experiment["parent_case_sha256"] == old["parent_case_sha256"]
        assert experiment["inventory_sha256"] == old["inventory_sha256"]
        assert {case_key(r) for r in summary["runs"]} == {case_key(r) for r in experiment["matrix"]}
        for relative, expected in experiment["source_sha256"].items():
            assert content_hash((root / "source-snapshot" / relative).read_text()) == expected
        for row in summary["runs"]:
            key = case_key(row)
            assert key not in seen, f"duplicate manual case: {key}"
            seen.add(key)
            run_id = f"{row['draw_id']}-h{row['scale_m']}".replace(".", "p")
            native = read(root / run_id / row["plan_id"] / "evaluation.json")
            baseline = read(reference / run_id / baseline_id / "evaluation.json")
            assert row["complete"] and row["source_verified"] and not row["invalid_reasons"]
            assert row["energy_error_J"] <= row["energy_tolerance_J"]
            assert native["diagnostics"]["parent_case_unchanged"]
            assert row["core_outcomes"] == native["metrics"]["core_outcomes"]
            assert row["intervention"] == native["intervention"]
            assert row["baseline_evaluation_sha256"] == baseline["evaluation_sha256"]
            assert row["baseline_core_outcomes"] == baseline["metrics"]["core_outcomes"]
            assert {o["building_id"] for o in row["core_outcomes"]} == core_ids
            assert row["protection_status"] == (
                "failed" if any(o["ignited"] for o in row["core_outcomes"]) else "passed"
            )
            source_key = row["draw_id"], row["source_id"]
            before = budgets.setdefault(source_key, row["source_budget"])
            for name, value in row["source_budget"].items():
                np.testing.assert_allclose(value, before[name], rtol=1e-8, atol=1e-6)
                if not row["source_replaced"]:
                    np.testing.assert_allclose(
                        value,
                        baseline["diagnostics"]["prescribed_source_budget"][name],
                        rtol=1e-8,
                        atol=1e-6,
                    )
            assert row["footprint_sha256"] == footprints.setdefault(
                source_key, row["footprint_sha256"]
            )
            ledger = row["intervention"]
            removed = set(ledger["demolished_ids"])
            assert not removed & core_ids and row["source_id"] not in removed
            union = unary_union([geometry[i] for i in removed])
            remaining = unary_union([g for i, g in geometry.items() if i not in removed])
            expected_areas = {
                "demolished_footprint_area_m2": sum(geometry[i].area for i in removed),
                "cleared_roof_union_area_m2": union.difference(remaining).area,
                "retained_shared_roof_area_m2": union.intersection(remaining).area,
            }
            for name, value in expected_areas.items():
                np.testing.assert_allclose(ledger[name], value, rtol=1e-7, atol=1e-5)
            baselines.add(run_id)
            rows.append(row)
    plans = []
    for plan_id in sorted({r["plan_id"] for r in rows}):
        selected = [r for r in rows if r["plan_id"] == plan_id]
        draws = []
        for draw_id in sorted({r["draw_id"] for r in selected}):
            scales = [r for r in selected if r["draw_id"] == draw_id]
            assert {r["scale_m"] for r in scales} == set(old["config"]["roof_scales_m"])
            draws.append(
                {
                    "draw_id": draw_id,
                    "source_id": scales[0]["source_id"],
                    "source_replaced": scales[0]["source_replaced"],
                    "core_ignited_any_scale": {
                        core: any(
                            o["ignited"]
                            for r in scales
                            for o in r["core_outcomes"]
                            if o["building_id"] == core
                        )
                        for core in sorted(core_ids)
                    },
                }
            )
        plans.append(
            {
                "plan_id": plan_id,
                "new_simulations": len(selected),
                "draws": draws,
                "core_failure_counts": {
                    core: sum(d["core_ignited_any_scale"][core] for d in draws)
                    for core in sorted(core_ids)
                },
                "intervention": selected[0]["intervention"],
            }
        )
    return {
        "schema_version": "heritage-bridge-manual-audit-1",
        "complete": True,
        "new_simulations": len(rows),
        "unique_saved_baselines_reused": len(baselines),
        "baseline_simulations_rerun": 0,
        "validation": {
            "frozen_matrices_complete": True,
            "producer_snapshots_verified": True,
            "core_source_energy_checks_passed": True,
            "same_source_polygons_and_budgets_preserved": True,
            "clearance_and_shared_areas_match_vector_geometry": True,
        },
        "plans": plans,
        "runs": rows,
        "whole_village_failure_estimate": None,
        "interpretation": "focused development draws; no heldout or global minimality claim",
        "optimization": False,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", nargs="+", type=Path, required=True)
    parser.add_argument("--reference", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.roots, args.reference)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(json.dumps({key: result[key] for key in ("new_simulations", "validation")}))
