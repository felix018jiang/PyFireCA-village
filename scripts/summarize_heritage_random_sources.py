"""Validate and summarize a completed explicit random-source heritage pilot."""

from __future__ import annotations

import argparse
import json
from itertools import combinations
from pathlib import Path

import numpy as np

from pyfireca.planning.heritage import content_hash


def summarize(output):
    output = Path(output)
    summary = json.loads((output / "summary.json").read_text())
    experiment = json.loads((output / "experiment.json").read_text())
    realized = json.loads((output / "realized-sources.json").read_text())
    if not summary["complete"] or len(summary["runs"]) != summary["planned_runs"]:
        raise ValueError("pilot is incomplete; missing runs cannot be counted as successes")
    if content_hash(experiment) != summary["experiment_sha256"]:
        raise ValueError("frozen experiment fingerprint mismatch")
    override_path = output / "execution-resource-override.json"
    override = json.loads(override_path.read_text()) if override_path.exists() else None
    if override is not None:
        assert override["frozen_experiment_sha256"] == content_hash(experiment)
        assert override["frozen_config_sha256"] == content_hash(experiment["config"])
        assert override["effective_max_radiation_edges"] > override["original_max_radiation_edges"]
        assert (
            content_hash((output / "execution-resource-source" / override["driver"]).read_text())
            == override["driver_content_sha256"]
        )
    for relative, expected in experiment["source_sha256"].items():
        actual = content_hash((output / "source-snapshot" / relative).read_text())
        if actual != expected:
            raise ValueError(f"producer snapshot mismatch: {relative}")
    assert summary["parent_case_unchanged"] and summary["inventory_unchanged"]
    protocol = experiment["source_protocol"]
    if "targeted_sampler_source_sha256" in protocol:
        assert (
            content_hash((output / "targeted-source-snapshot.py").read_text())
            == protocol["targeted_sampler_source_sha256"]
        )
    if "prescribed_sampler_source_sha256" in protocol:
        assert (
            content_hash((output / "prescribed-source-snapshot.py").read_text())
            == protocol["prescribed_sampler_source_sha256"]
        )
    by_key = {(row["plan_id"], row["draw_id"], row["scale_m"]): row for row in summary["runs"]}
    assert len(by_key) == summary["planned_runs"]
    budgets, footprints, records = {}, {}, []
    for row in summary["runs"]:
        run_id = f"{row['draw_id']}-h{row['scale_m']}".replace(".", "p")
        native = json.loads((output / run_id / row["plan_id"] / "evaluation.json").read_text())
        assert native["evaluation_sha256"] == row["evaluation_sha256"]
        assert native["metrics"]["core_outcomes"] == row["core_outcomes"]
        execution_hash = native["diagnostics"].get("execution_resource_override_sha256")
        if execution_hash is not None:
            assert override is not None and execution_hash == content_hash(override)
            assert (
                native["diagnostics"]["effective_max_radiation_edges"]
                == override["effective_max_radiation_edges"]
            )
        assert row["complete"] and row["source_verified"] and not row["invalid_reasons"]
        assert row["energy_error_J"] <= row["energy_tolerance_J"]
        assert {o["building_id"] for o in row["core_outcomes"]} == {
            target["building_id"] for target in experiment["study"]["targets"]
        }
        key = (row["draw_id"], row["source_id"])
        before = budgets.setdefault(key, row["source_budget"])
        for name, value in row["source_budget"].items():
            np.testing.assert_allclose(value, before[name], rtol=1e-8, atol=1e-6)
        assert row["footprint_sha256"] == footprints.setdefault(key, row["footprint_sha256"])
        np.testing.assert_allclose(
            row["source_budget"]["covered_area_m2"],
            protocol["ignition_area_m2"],
            rtol=1e-6,
            atol=1e-6,
        )
    for plan in summary["plans"]:
        assert plan["all_evaluations_complete"]
        np.testing.assert_allclose(sum(r["weight"] for r in realized[plan["plan_id"]]), 1)
        for stratum in protocol["strata"]:
            draws = [r for r in plan["draws"] if r.get("stratum") == stratum]
            records.append(
                {
                    "plan_id": plan["plan_id"],
                    "stratum": stratum,
                    "draws": len(draws),
                    "joint_failures": sum(r["status"] == "failed" for r in draws),
                    "core_failures": {
                        t["building_id"]: sum(
                            r["core_ignited"].get(t["building_id"], False) for r in draws
                        )
                        for t in experiment["study"]["targets"]
                    },
                }
            )
    plan_ids = [p["plan_id"] for p in experiment["config"]["plans"]]
    source_rows = {p: {r["draw_id"]: r for r in realized[p]} for p in plan_ids}
    paired = []
    for baseline_id, treated_id in combinations(plan_ids, 2):
        for draw in protocol["draws"]:
            for scale in experiment["config"]["roof_scales_m"]:
                before_source = source_rows[baseline_id][draw["draw_id"]]
                after_source = source_rows[treated_id][draw["draw_id"]]
                empty = any(s["status"] == "empty_stratum" for s in (before_source, after_source))
                before = by_key.get((baseline_id, draw["draw_id"], scale))
                after = by_key.get((treated_id, draw["draw_id"], scale))
                if not empty:
                    assert before is not None and after is not None
                paired.append(
                    {
                        "baseline_plan_id": baseline_id,
                        "treated_plan_id": treated_id,
                        "draw_id": draw["draw_id"],
                        "scale_m": scale,
                        "comparison_status": "zero_weight_empty_stratum" if empty else "evaluated",
                        "same_source": not empty and before["source_id"] == after["source_id"],
                        "baseline_failed_ids": (
                            [o["building_id"] for o in before["core_outcomes"] if o["ignited"]]
                            if before is not None
                            else None
                        ),
                        "treated_failed_ids": (
                            [o["building_id"] for o in after["core_outcomes"] if o["ignited"]]
                            if after is not None
                            else None
                        ),
                    }
                )
    return {
        **summary,
        "stratum_outcomes": records,
        "paired_core_outcomes": paired,
        "validation": {
            "all_planned_runs_complete_valid": True,
            "all_core_identities_present": True,
            "all_source_budgets_equal_across_plans_and_scales_when_same_source": True,
            "all_source_polygons_equal_when_same_source": True,
            "all_energy_checks_passed": True,
            "frozen_producer_snapshot_verified": True,
            "execution_resource_override_verified": override is not None,
            "execution_resource_override": override,
            "maximum_energy_error_J": max(r["energy_error_J"] for r in summary["runs"]),
            "minimum_energy_tolerance_J": min(r["energy_tolerance_J"] for r in summary["runs"]),
        },
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    result = summarize(args.input)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                "runs": len(result["runs"]),
                "plans": result["plans"],
                "validation": result["validation"],
            },
            ensure_ascii=False,
        )
    )
