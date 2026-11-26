"""Independently certify the completed eight-candidate problem and replay one case."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import sys
from math import fsum
from pathlib import Path

import numpy as np
from shapely.geometry import Polygon
from shapely.ops import unary_union


def read(path):
    return json.loads(Path(path).read_text())


def sha(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def audit(root, *, restore_native=True):
    root = Path(root).resolve()
    receipt, result = read(root / "continuation.json"), read(root / "search-result.json")
    parent = Path(receipt["parent_root"])
    sys.path.insert(0, str(parent / "source-snapshot/src"))
    from pyfireca.planning.heritage import content_hash

    assert sha(root / "driver-snapshot.py") == receipt["driver_sha256"]
    for path, expected in receipt["parent_files_sha256"].items():
        assert sha(parent / path) == expected
    experiment = read(parent / "experiment.json")
    problem, config = experiment["problem"], receipt["config"]
    assert content_hash(problem) == result["problem_sha256"] == receipt["problem_sha256"]
    for path, expected in experiment["source_sha256"].items():
        assert content_hash((parent / "source-snapshot" / path).read_text()) == expected
    inventory = read(parent / "inventory.json")
    geometries = {
        b["building_id"]: unary_union([Polygon(p["exterior"], p["holes"]) for p in b["polygons"]])
        for b in inventory["buildings"]
    }
    candidates = sorted(problem["candidate_ids"])
    areas = {i: geometries[i].area for i in candidates}
    cores = {t["building_id"] for t in inventory["targets"]}
    scenarios = [f"{label}-h{s:g}" for label in problem["fires"] for s in problem["scales_m"]]
    pending = {k: {tuple(p) for p in config["pending_plans"][str(k)]} for k in (2, 3)}
    preflight = read(
        Path(__file__).resolve().parents[1]
        / "docs/results/heritage-exact-expansion-preflight-20261024.json"
    )
    bounds = {
        (r["fire_id"], r["scale_m"]): r for r in preflight["layouts"] if r["scope"] == "expanded8"
    }
    checks, ledgers, source_budgets = {}, {}, {}
    new_count, timing = 0, {}
    for source_root in (parent, root):
        for path in sorted(source_root.glob("evaluations/*/check.json")):
            check, record = read(path), read(path.with_name("evaluation.json"))
            ids, scenario = tuple(check["demolished_ids"]), check["scenario_id"]
            key = ids, scenario
            assert key not in checks
            assert check["problem_sha256"] == result["problem_sha256"]
            assert scenario == record["scenario_id"] and scenario in scenarios
            assert sorted(record["intervention"]["demolished_ids"]) == list(ids)
            assert not set(ids) & cores
            diag, metric, evidence = record["diagnostics"], record["metrics"], check["evidence"]
            assert record["evaluation_sha256"] == evidence["evaluation_sha256"]
            assert (
                diag["source_verified"]
                and diag["parent_case_unchanged"]
                and not diag["invalid_reasons"]
            )
            assert max(diag["energy_closure_errors_J"].values()) <= diag["energy_tolerance_J"]
            assert metric["core_outcomes"] == evidence["core_outcomes"]
            assert {o["building_id"] for o in metric["core_outcomes"]} == cores
            assert metric["protection_status"] == check["status"]
            if check["status"] == "passed":
                assert diag["reached_requested_horizon"] and not any(
                    o["ignited"] for o in metric["core_outcomes"]
                )
            else:
                assert check["status"] == "failed" and any(
                    o["ignited"] for o in metric["core_outcomes"]
                )
            fire_id = scenario.rsplit("-h", 1)[0]
            fire = problem["fires"][fire_id]
            actual_source = next(i for i in fire["footprints"] if i not in ids)
            assert evidence["source_id"] == actual_source
            assert diag["roof_resolution_protocol"]["refined_ids"] == fire["refined_ids"]
            capacity = bounds[fire_id, evidence["scale_m"]]
            assert evidence["nodes"] == capacity["nodes"]
            assert evidence["radiation_edges"] <= capacity["integrated_edges_upper_bound"]
            budget = evidence["source_budget"]
            assert np.isclose(budget["covered_area_m2"], 25, rtol=1e-8, atol=1e-6)
            ref = source_budgets.setdefault((fire_id, actual_source), budget)
            for name in budget:
                np.testing.assert_allclose(budget[name], ref[name], rtol=1e-8, atol=1e-6)
            checks[key] = check
            ledgers.setdefault(ids, record["intervention"])
            if source_root == root:
                assert ids in pending[len(ids)]
                new_count += 1
                timing.setdefault(str(evidence["scale_m"]), []).append(evidence["elapsed_s"])
    assert len(checks) - new_count == 48
    assert new_count <= config["budget"]["max_new_checks"]
    for k in (2, 3):
        assert (
            sum(len(ids) == k for ids, _ in checks if ids in pending[k])
            <= config["budget"]["stage_limits"][str(k)]
        )
    for ids, ledger in ledgers.items():
        union = unary_union([geometries[i] for i in ids])
        retained = unary_union([g for i, g in geometries.items() if i not in ids])
        for field, expected in (
            ("demolished_footprint_area_m2", fsum(areas[i] for i in ids)),
            ("cleared_roof_union_area_m2", union.difference(retained).area),
            ("retained_shared_roof_area_m2", union.intersection(retained).area),
        ):
            assert np.isclose(ledger[field], expected, rtol=1e-8, atol=1e-5), (ids, field)

    def classify(ids):
        rows = [checks.get((ids, s)) for s in scenarios]
        failures = [c for c in rows if c is not None and c["status"] == "failed"]
        status = (
            "failed"
            if failures
            else "passed"
            if all(c is not None and c["status"] == "passed" for c in rows)
            else "unknown"
        )
        return status, failures, sum(c is not None for c in rows)

    plans = [p for k in range(len(candidates) + 1) for p in itertools.combinations(candidates, k)]
    passed = [p for p in plans if classify(p)[0] == "passed"]
    best = min(passed, key=lambda p: (len(p), fsum(areas[i] for i in p)))
    objective = (len(best), fsum(areas[i] for i in best))
    table = []
    for ids in plans:
        status, failures, used = classify(ids)
        value = (len(ids), fsum(areas[i] for i in ids))
        if status == "unknown" and len(ids) > len(best):
            status = "cardinality_bound"
        elif status == "unknown" and value >= objective:
            status = "area_bound" if value > objective else "objective_tie"
        table.append(
            {
                "demolished_ids": list(ids),
                "objective": list(value),
                "status": status,
                "raw_checks": used,
                "failure_witness": None
                if not failures
                else {
                    "source_id": failures[0]["evidence"]["source_id"],
                    "scale_m": failures[0]["evidence"]["scale_m"],
                    "core_outcomes": failures[0]["evidence"]["core_outcomes"],
                },
            }
        )
    unresolved = [r for r in table if r["status"] == "unknown"]
    assert not unresolved and result["optimality_proven"] and result["status"] == "optimal"
    assert result["best_ids"] == list(best) and result["best_objective"] == list(objective)
    pair_status = [
        {"demolished_ids": list(p), "status": classify(p)[0]} for p in sorted(pending[2])
    ]
    assert all(r["status"] in {"passed", "failed"} for r in pair_status)
    if any(r["status"] == "passed" for r in pair_status):
        assert not any(len(p) == 3 for p, _ in checks if p in pending[3])
    restoration = None
    if restore_native:
        native = list(root.glob("evaluations/*/native"))
        assert len(native) == 1
        native = native[0]
        spec = importlib.util.spec_from_file_location(
            "eight_restore", parent / "source-snapshot/scripts/run_heritage_roof_study.py"
        )
        physical_io = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(physical_io)
        bundle = physical_io.load_v3_case_bundle(native / "node-storage-not-map")
        record = read(native / "evaluation.json")
        assert (
            physical_io.request_fingerprint(bundle.request)
            == record["diagnostics"]["physical_request_sha256"]
        )
        params = physical_io.FlameContactParameters.from_mapping(read(native / "contact.json"))
        with np.load(native / "contact.npz") as saved:
            contact = physical_io.FlameContactOperator(
                saved["neighbors"], saved["geometry_fraction"], params, bundle.request.parameters
            )
        layout = read(native / "layout.json")
        nodes = np.asarray(
            [r["node"] for r in layout["roof_components"] if set(r["building_ids"]) & cores]
        )
        assert len(nodes)
        replay = physical_io._run_resolved_v3_simulation(
            bundle.request,
            contact=contact,
            retain_snapshots=False,
            stop_when=lambda state: bool(np.isfinite(state.ignition_time_s.ravel()[nodes]).any()),
        )
        physical_io.check_final(bundle.request, replay)
        with np.load(native / "final-state.npz") as expected:
            for field in replay.final_state.__dataclass_fields__:
                np.testing.assert_array_equal(getattr(replay.final_state, field), expected[field])
            np.testing.assert_array_equal(replay.ignition_cause, expected["ignition_cause"])
        restoration = {
            "all_final_state_fields_equal": True,
            "saved_contact_used": True,
            "first_core_stop_rule_preserved": True,
        }
    counts = {s: sum(r["status"] == s for r in table) for s in sorted({r["status"] for r in table})}
    return {
        "schema_version": "heritage-exact-eight-certificate-1",
        "audit_passed": True,
        "complete": True,
        "problem_sha256": result["problem_sha256"],
        "search_result_sha256": content_hash(result),
        "parent_checks_reused": 48,
        "new_search_CA_checks": new_count,
        "total_search_CA_checks": len(checks),
        "new_native_validation_CA_checks": int(restore_native),
        "whole_village_search_started": False,
        "best_ids": list(best),
        "best_objective": list(objective),
        "subset_classification_counts": counts,
        "zero_single_and_pair_all_failed": all(
            classify(p)[0] == "failed" for p in plans if len(p) < 3
        ),
        "new_pair_results": pair_status,
        "new_triple_results": [r for r in table if tuple(r["demolished_ids"]) in pending[3]],
        "unverified_better_plan_count": 0,
        "native_restoration": restoration,
        "source_energy_identity_geometry_capacity_and_producer_verified": True,
        "timing_s": {
            s: {"count": len(t), "sum": sum(t), "median": float(np.median(t))}
            for s, t in timing.items()
        },
        "best_area_ledger": ledgers[best],
        "best_core_results": [{"scenario_id": s, **checks[best, s]["evidence"]} for s in scenarios],
        "subset_table": table,
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    report = audit(args.root)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    print(
        json.dumps(
            {
                k: report[k]
                for k in (
                    "complete",
                    "best_ids",
                    "new_search_CA_checks",
                    "unverified_better_plan_count",
                )
            }
        )
    )
