"""Audit the budgeted expanded search, including all unproved better plans."""

from __future__ import annotations

import argparse
import importlib.util
import itertools
import json
from math import fsum
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union

from pyfireca.planning.heritage import content_hash
from pyfireca.village.physics_v3 import V3CellPhysicsArrays


def read(path):
    return json.loads(Path(path).read_text())


def audit(root, *, restore_native=True, reference_root=None):
    root = Path(root).resolve()
    experiment, result = read(root / "experiment.json"), read(root / "search-result.json")
    config, problem = experiment["config"], experiment["problem"]
    preflight_path = (
        Path(__file__).resolve().parents[1]
        / "docs/results/heritage-exact-expansion-preflight-20261024.json"
    )
    preflight = read(preflight_path)
    assert preflight["config_sha256"] == content_hash(config)
    bounds = {
        (row["fire_id"], row["scale_m"]): row
        for row in preflight["layouts"]
        if row["scope"] == "expanded8"
    }
    assert result["problem_sha256"] == content_hash(problem)
    for relative, expected in experiment["source_sha256"].items():
        assert content_hash((root / "source-snapshot" / relative).read_text()) == expected
    inventory = read(root / "inventory.json")
    areas = {
        b["building_id"]: unary_union(
            [Polygon(p["exterior"], p["holes"]) for p in b["polygons"]]
        ).area
        for b in inventory["buildings"]
    }
    cores = {t["building_id"] for t in inventory["targets"]}
    scenarios = [f"{f}-h{s:g}" for f in problem["fires"] for s in problem["scales_m"]]
    saved, source_budgets, initial, times = {}, {}, [], {}
    for path in sorted((root / "evaluations").glob("*/check.json")):
        check, record = read(path), read(path.with_name("evaluation.json"))
        ids, scenario = tuple(check["demolished_ids"]), check["scenario_id"]
        assert check["problem_sha256"] == result["problem_sha256"]
        assert check["evidence"]["evaluation_sha256"] == record["evaluation_sha256"]
        assert scenario == record["scenario_id"] and scenario in scenarios
        assert sorted(record["intervention"]["demolished_ids"]) == list(ids)
        assert not set(ids) & cores
        diag, outcomes = record["diagnostics"], record["metrics"]["core_outcomes"]
        assert diag["source_verified"] and diag["parent_case_unchanged"]
        assert not diag["invalid_reasons"]
        assert max(diag["energy_closure_errors_J"].values()) <= diag["energy_tolerance_J"]
        assert check["evidence"]["core_outcomes"] == outcomes
        assert {o["building_id"] for o in outcomes} == cores
        assert check["status"] == record["metrics"]["protection_status"]
        if check["status"] == "passed":
            assert diag["reached_requested_horizon"] and not any(o["ignited"] for o in outcomes)
        else:
            assert check["status"] == "failed" and any(o["ignited"] for o in outcomes)
        budget = check["evidence"]["source_budget"]
        assert np.isclose(budget["covered_area_m2"], 25, rtol=1e-8, atol=1e-6)
        key = scenario.rsplit("-h", 1)[0], check["evidence"]["source_id"]
        capacity = bounds[key[0], check["evidence"]["scale_m"]]
        assert check["evidence"]["nodes"] == capacity["nodes"]
        assert check["evidence"]["radiation_edges"] <= capacity["integrated_edges_upper_bound"]
        reference = source_budgets.setdefault(key, budget)
        for field in budget:
            np.testing.assert_allclose(budget[field], reference[field], rtol=1e-8, atol=1e-6)
        source = check["evidence"]["source_id"]
        assert source not in ids
        fire = problem["fires"][key[0]]
        assert source == next(i for i in fire["footprints"] if i not in ids)
        assert diag["roof_resolution_protocol"]["refined_ids"] == fire["refined_ids"]
        saved[ids, scenario] = check
        times.setdefault(str(check["evidence"]["scale_m"]), []).append(
            check["evidence"]["elapsed_s"]
        )
        if list(ids) == sorted(config["initial_ids"]):
            initial.append(
                {"scenario_id": scenario, "source_id": source, "core_outcomes": outcomes}
            )
    assert len(saved) <= config["max_total_checks"]
    assert len(initial) == len(scenarios)
    assert all(
        saved[tuple(sorted(config["initial_ids"])), s]["status"] == "passed" for s in scenarios
    )
    best_ids = tuple(result["best_ids"])
    best_objective = [len(best_ids), fsum(areas[i] for i in best_ids)]
    assert best_objective == result["best_objective"]
    assert all(saved[best_ids, s]["status"] == "passed" for s in scenarios)
    table = []
    for count in range(len(config["candidate_ids"]) + 1):
        for ids in itertools.combinations(sorted(config["candidate_ids"]), count):
            objective = [count, fsum(areas[i] for i in ids)]
            checks = [saved.get((ids, s)) for s in scenarios]
            failures = [c for c in checks if c is not None and c["status"] == "failed"]
            if failures:
                status = "failed"
            elif all(c is not None and c["status"] == "passed" for c in checks):
                status = "passed"
            elif count > len(best_ids):
                status = "cardinality_bound"
            elif count == len(best_ids) and objective[1] > best_objective[1]:
                status = "area_bound"
            elif any(i in result["dominance_certificates"] for i in ids):
                status = "strict_identity_dominance"
            else:
                status = "unverified"
            table.append(
                {
                    "demolished_ids": list(ids),
                    "objective": objective,
                    "status": status,
                    "better_than_incumbent": objective < best_objective,
                    "raw_checks": sum(c is not None for c in checks),
                    "failure_witness": None
                    if not failures
                    else {
                        "source_id": failures[0]["evidence"]["source_id"],
                        "scale_m": failures[0]["evidence"]["scale_m"],
                        "ignited_core_ids": [
                            o["building_id"]
                            for o in failures[0]["evidence"]["core_outcomes"]
                            if o["ignited"]
                        ],
                    },
                }
            )
    unresolved = [r for r in table if r["status"] == "unverified" and r["better_than_incumbent"]]
    assert result["optimality_proven"] == (not unresolved)
    count_lower_bound = min(
        r["objective"][0] for r in table if r["status"] in {"passed", "unverified"}
    )
    counts = {s: sum(r["status"] == s for r in table) for s in {r["status"] for r in table}}
    classified = [r for r in table if r["status"] in {"passed", "failed"}]
    classified_raw_checks = sum(r["raw_checks"] for r in classified)
    for row in result["trace"]:
        for check in row["checks"]:
            raw = saved[tuple(row["demolished_ids"]), check["scenario_id"]]
            assert raw["status"] == check["status"] and raw["evidence"] == check["evidence"]
    native = list((root / "evaluations").glob("*/native"))
    assert len(native) == 1
    unchanged_case = None
    if reference_root is not None:
        old_root = Path(reference_root).resolve()
        old_exp = read(old_root / "experiment.json")
        assert old_exp["problem"]["case_sha256"] == problem["case_sha256"]
        previous = old_root / native[0].relative_to(root)
        previous_record, current_record = (
            read(p / "evaluation.json") for p in (previous, native[0])
        )
        assert previous_record["scenario_id"] == current_record["scenario_id"]
        fields = V3CellPhysicsArrays.__dataclass_fields__
        for name in fields:
            np.testing.assert_array_equal(
                np.load(previous / "node-storage-not-map" / f"{name}.npy", mmap_mode="r"),
                np.load(native[0] / "node-storage-not-map" / f"{name}.npy", mmap_mode="r"),
            )
        for filename in ("layout.npz", "contact.npz", "final-state.npz"):
            with np.load(previous / filename) as a, np.load(native[0] / filename) as b:
                assert a.files == b.files
                for field in a.files:
                    np.testing.assert_array_equal(a[field], b[field])
        unchanged_case = {
            "reference_root": str(old_root),
            "thermal_parameter_arrays_identical": len(fields),
            "layout_contact_and_all_final_arrays_identical": True,
            "scope": "same_B0075_fire_first_scale_initial_triple;not_other_refinement_domains",
        }
    restoration = None
    if restore_native:
        spec = importlib.util.spec_from_file_location(
            "restore_expanded_producer", root / "source-snapshot/scripts/run_heritage_roof_study.py"
        )
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        restoration = runner.restore_simulation(native[0])
    return {
        "schema_version": "heritage-exact-expansion-audit-1",
        "audit_passed": True,
        "search_complete": result["optimality_proven"],
        "search_status": result["status"],
        "problem_sha256": result["problem_sha256"],
        "preflight_sha256": content_hash(preflight),
        "search_result_sha256": content_hash(result),
        "candidate_count": len(config["candidate_ids"]),
        "valid_CA_checks": len(saved),
        "new_native_validation_CA_checks": int(restore_native),
        "raw_check_limit": config["max_total_checks"],
        "best_ids": result["best_ids"],
        "best_objective": result["best_objective"],
        "independently_proven_count_lower_bound": count_lower_bound,
        "verified_feasible_count_upper_bound": len(best_ids),
        "count_gap": len(best_ids) - count_lower_bound,
        "subset_classification_counts": counts,
        "fully_classified_observed_plans": len(classified),
        "raw_checks_for_classified_plans": classified_raw_checks,
        "checks_for_same_plans_if_all_eight_were_requested": len(classified) * len(scenarios),
        "avoided_later_scenario_requests_on_classified_plans": len(classified) * len(scenarios)
        - classified_raw_checks,
        "this_is_request_reduction_on_classified_plans_not_a_full_search_speedup": True,
        "unverified_better_plan_count": len(unresolved),
        "unverified_better_plan_count_by_cardinality": {
            str(k): sum(r["objective"][0] == k for r in unresolved)
            for k in range(len(best_ids) + 1)
        },
        "initial_solution_all_eight_checks_passed": True,
        "initial_core_results": initial,
        "budget_stop_reason": result["budget_stop_reason"],
        "producer_source_budget_energy_core_and_common_layout_verified": True,
        "native_restoration": restoration,
        "unchanged_reference_case": unchanged_case,
        "timing_s": {
            scale: {"count": len(t), "sum": sum(t), "median": float(np.median(t)), "max": max(t)}
            for scale, t in times.items()
        },
        "full_matrix_checks_without_rejection_or_bounds": len(table) * len(scenarios),
        "zero_to_three_full_matrix_checks_without_rejection_or_area_bounds": sum(
            r["objective"][0] <= 3 for r in table
        )
        * len(scenarios),
        "these_workload_counts_are_not_a_measured_unpruned_runtime": True,
        "whole_village_optimality_proven": False,
        "subset_table": table,
        "search_result": result,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--reference", type=Path)
    args = parser.parse_args()
    report = audit(args.root, reference_root=args.reference)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "audit_passed",
                    "search_status",
                    "valid_CA_checks",
                    "unverified_better_plan_count",
                )
            }
        )
    )
