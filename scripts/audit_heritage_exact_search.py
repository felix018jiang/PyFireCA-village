"""Independently certify the small saved subset table and restore one native solve."""

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


def read(path):
    return json.loads(Path(path).read_text())


def audit(root):
    root = Path(root).resolve()
    experiment, result = read(root / "experiment.json"), read(root / "search-result.json")
    assert result["problem_sha256"] == content_hash(experiment["problem"])
    for relative, expected in experiment["source_sha256"].items():
        assert content_hash((root / "source-snapshot" / relative).read_text()) == expected
    inventory = read(root / "inventory.json")
    areas = {
        b["building_id"]: unary_union(
            [Polygon(p["exterior"], p["holes"]) for p in b["polygons"]]
        ).area
        for b in inventory["buildings"]
    }
    core_ids = {t["building_id"] for t in inventory["targets"]}
    scenarios = {
        f"{f}-h{s:g}"
        for f in experiment["problem"]["fires"]
        for s in experiment["problem"]["scales_m"]
    }
    saved, budgets = {}, {}
    for path in sorted((root / "evaluations").glob("*/check.json")):
        check, evaluation = read(path), read(path.with_name("evaluation.json"))
        ids, scenario = tuple(check["demolished_ids"]), check["scenario_id"]
        assert check["problem_sha256"] == result["problem_sha256"]
        assert evaluation["evaluation_sha256"] == check["evidence"]["evaluation_sha256"]
        assert sorted(evaluation["intervention"]["demolished_ids"]) == list(ids)
        assert evaluation["scenario_id"] == scenario
        diagnostics = evaluation["diagnostics"]
        assert diagnostics["source_verified"] and diagnostics["parent_case_unchanged"]
        assert not diagnostics["invalid_reasons"]
        assert (
            max(diagnostics["energy_closure_errors_J"].values())
            <= diagnostics["energy_tolerance_J"]
        )
        outcomes = evaluation["metrics"]["core_outcomes"]
        assert {o["building_id"] for o in outcomes} == core_ids
        if check["status"] == "passed":
            assert diagnostics["reached_requested_horizon"]
            assert all(not o["ignited"] for o in outcomes)
        else:
            assert check["status"] == "failed" and any(o["ignited"] for o in outcomes)
        budget = check["evidence"]["source_budget"]
        assert np.isclose(budget["covered_area_m2"], 25, rtol=1e-8, atol=1e-6)
        key = (scenario.rsplit("-h", 1)[0], check["evidence"]["source_id"])
        reference = budgets.setdefault(key, budget)
        for field in budget:
            np.testing.assert_allclose(budget[field], reference[field], rtol=1e-8, atol=1e-6)
        saved[ids, scenario] = check
    table = []
    candidates = sorted(result["candidate_ids"])
    for count in range(len(candidates) + 1):
        for ids in itertools.combinations(candidates, count):
            checks = [saved.get((ids, scenario)) for scenario in scenarios]
            failures = [c for c in checks if c is not None and c["status"] == "failed"]
            status = (
                "failed"
                if failures
                else "passed"
                if all(c is not None and c["status"] == "passed" for c in checks)
                else "unknown"
            )
            assert status != "unknown", "subset lacks either valid witness or full passage"
            table.append(
                {
                    "demolished_ids": list(ids),
                    "status": status,
                    "objective": [count, fsum(areas[i] for i in ids)],
                }
            )
    best = min((r for r in table if r["status"] == "passed"), key=lambda r: r["objective"])
    assert best["demolished_ids"] == result["best_ids"]
    assert best["objective"] == result["best_objective"]
    assert result["optimality_proven"] and not result["proof_blockers"]
    spec = importlib.util.spec_from_file_location(
        "audit_exact_runner", Path(__file__).with_name("run_heritage_roof_study.py")
    )
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    native = list((root / "evaluations").glob("*/native"))
    assert len(native) == 1
    restoration = runner.restore_simulation(native[0])
    return {
        "schema_version": "heritage-exact-small-audit-1",
        "complete": True,
        "problem_sha256": result["problem_sha256"],
        "search_result_sha256": content_hash(result),
        "all_subsets_independently_classified": len(table),
        "valid_CA_checks": len(saved),
        "optimality_proven_within_candidate_set": True,
        "best_ids": best["demolished_ids"],
        "best_objective": best["objective"],
        "full_dual_scale_passage_required": True,
        "same_source_budgets_and_25m2_area_verified": True,
        "energy_core_identity_and_producer_snapshots_verified": True,
        "native_restoration": restoration,
        "subset_table": table,
        "search_result": result,
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
            {k: report[k] for k in ("complete", "valid_CA_checks", "best_ids", "best_objective")}
        )
    )
