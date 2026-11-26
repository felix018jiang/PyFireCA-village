"""Run the reusable strategy layer against saved physical checks; never run CA."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from pyfireca.planning.building_search import ProtectionCheck
from pyfireca.planning.heritage import content_hash
from pyfireca.planning.heritage_strategy import plan_heritage_strategy
from pyfireca.planning.study_io import load_study


def read(path):
    return json.loads(Path(path).read_text())


def replay(study_path, physical_root, additional_root=None, costs_path=None, max_lookups=4096):
    _, _, inventory = load_study(study_path)
    fees = None if costs_path is None else read(costs_path)
    return replay_inventory(inventory, physical_root, additional_root, fees, max_lookups)


def replay_inventory(inventory, physical_root, additional_root=None, fees=None, max_lookups=4096):
    physical_root = Path(physical_root).resolve()
    experiment = read(physical_root / "experiment.json")
    problem, signature = experiment["problem"], experiment["problem_sha256"]
    assert content_hash(problem) == signature
    assert inventory.sha256 == problem["inventory_sha256"]
    roots = [physical_root]
    if additional_root is not None:
        extra = Path(additional_root).resolve()
        assert read(extra / "continuation.json")["problem_sha256"] == signature
        roots.append(extra)
    saved = {}
    for root in roots:
        for path in sorted(root.glob("evaluations/*/check.json")):
            row = read(path)
            assert row["problem_sha256"] == signature
            key = tuple(row["demolished_ids"]), row["scenario_id"]
            assert key not in saved
            saved[key] = row
    hits, absent = 0, 0

    def evaluate(ids, scenario):
        nonlocal hits, absent
        row = saved.get((ids, scenario))
        if row is None:
            absent += 1
            return ProtectionCheck("unknown", {"reason": "not_simulated_in_saved_physical_problem"})
        hits += 1
        return ProtectionCheck(row["status"], row["evidence"])

    scenarios = tuple(f"{label}-h{s:g}" for label in problem["fires"] for s in problem["scales_m"])
    result = plan_heritage_strategy(
        inventory,
        scenarios,
        evaluate,
        physical_problem_sha256=signature,
        candidate_ids=problem["candidate_ids"],
        initial_ids=experiment["config"]["initial_ids"],
        declared_cost_by_id=None if fees is None else fees["values"],
        cost_unit=None if fees is None else fees["unit"],
        max_new_checks=max_lookups,
    )
    return {
        "schema_version": "heritage-reusable-strategy-replay-1",
        "physical_problem_sha256": signature,
        "new_physical_CA_checks": 0,
        "saved_physical_records": len(saved),
        "physical_record_hits": hits,
        "uncomputed_scenario_lookups": absent,
        "uncomputed_results_never_assumed_successful": True,
        "lookup_count_is_not_CA_execution_count": True,
        "max_oracle_lookups": max_lookups,
        "best_ids": result["best_ids"],
        "best_objective": result["best_objective"],
        "optimality_proven": result["optimality_proven"],
        "strategy_result": result,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--study", type=Path, required=True)
    parser.add_argument("--physical-root", type=Path, required=True)
    parser.add_argument("--additional-root", type=Path)
    parser.add_argument("--costs", type=Path)
    parser.add_argument("--max-lookups", type=int, default=4096)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = replay(
        args.study, args.physical_root, args.additional_root, args.costs, args.max_lookups
    )
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {k: report[k] for k in ("new_physical_CA_checks", "best_ids", "optimality_proven")}
        )
    )
