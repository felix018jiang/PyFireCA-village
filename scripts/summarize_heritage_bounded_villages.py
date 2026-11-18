"""Read-only bounded-search and fixed-winner confirmation evidence table."""

from __future__ import annotations

import argparse
from pathlib import Path

from pyfireca.planning.heritage import content_hash
from pyfireca.planning.strategy_io import file_sha256, read_json, write_json


def checked_rows(root):
    experiment = read_json(root / "experiment.json")
    problem, config = experiment["problem"], experiment["config"]
    assert content_hash(problem) == experiment["problem_sha256"]
    for relative, digest in experiment["source_sha256"].items():
        assert content_hash((root / "source-snapshot" / relative).read_text()) == digest
    for path, digest in experiment["source_references_sha256"].items():
        assert file_sha256(path) == digest
    rows = []
    for path in sorted(root.glob("evaluations/*/check.json")):
        row, record = read_json(path), read_json(path.with_name("evaluation.json"))
        evidence, diagnostics = row["evidence"], record["diagnostics"]
        assert row["problem_sha256"] == experiment["problem_sha256"]
        assert evidence["evaluation_sha256"] == record["evaluation_sha256"]
        assert row["status"] in {"passed", "failed"}
        assert diagnostics["source_verified"] and diagnostics["parent_case_unchanged"]
        assert not diagnostics["invalid_reasons"]
        assert abs(evidence["source_budget"]["covered_area_m2"] - 25) < 1e-4
        assert all(
            abs(value) <= diagnostics["energy_tolerance_J"]
            for value in diagnostics["energy_closure_errors_J"].values()
        )
        assert row["demolished_ids"] == record["intervention"]["demolished_ids"]
        assert record["intervention"]["retained_component_parameters_unchanged"]
        fire = problem["fires"][row["scenario_id"].rsplit("-h", 1)[0]]
        actual_source = next(i for i in fire["footprints"] if i not in row["demolished_ids"])
        assert actual_source == evidence["source_id"]
        core_ids = {t["building_id"] for t in experiment["study"]["targets"]}
        assert {c["building_id"] for c in evidence["core_outcomes"]} == core_ids
        assert not set(row["demolished_ids"]) & core_ids
        if row["status"] == "passed":
            assert diagnostics["reached_requested_horizon"]
        rows.append(
            {
                "demolished_ids": row["demolished_ids"],
                "scenario_id": row["scenario_id"],
                "source_id": actual_source,
                "source_replaced": evidence["source_replaced"],
                "scale_m": evidence["scale_m"],
                "status": row["status"],
                "observed_until_s": diagnostics["simulated_until_s"],
                "reached_horizon": diagnostics["reached_requested_horizon"],
                "core_outcomes": evidence["core_outcomes"],
                "nominal_area_m2": record["intervention"]["demolished_footprint_area_m2"],
                "cleared_area_m2": record["intervention"]["cleared_roof_union_area_m2"],
                "shared_retained_area_m2": record["intervention"]["retained_shared_roof_area_m2"],
            }
        )
    assert len(rows) <= config["max_total_checks"]
    return experiment, rows


def pair_scales(rows):
    groups = {}
    for row in rows:
        key = tuple(row["demolished_ids"]), row["scenario_id"].rsplit("-h", 1)[0]
        groups.setdefault(key, []).append(row)
    paired = []
    for (ids, fire), items in sorted(groups.items()):
        complete = {r["scale_m"] for r in items} == {1.0, 0.5}
        protected = complete and all(r["status"] == "passed" for r in items)
        core_ids = {c["building_id"] for r in items for c in r["core_outcomes"]}
        cores = []
        for identifier in sorted(core_ids):
            observations = [
                {"scale_m": r["scale_m"], "observed_until_s": r["observed_until_s"], **c}
                for r in items
                for c in r["core_outcomes"]
                if c["building_id"] == identifier
            ]
            failed = any(c["ignited"] for c in observations)
            full = complete and all(r["reached_horizon"] for r in items)
            cores.append(
                {
                    "building_id": identifier,
                    "classification": "failed" if failed else "protected" if full else "censored",
                    "observations": observations,
                }
            )
        paired.append(
            {
                "demolished_ids": list(ids),
                "fire_id": fire,
                "both_scales_present": complete,
                "joint_protected": protected,
                "source_ids_by_scale": {str(r["scale_m"]): r["source_id"] for r in items},
                "cores": cores,
            }
        )
    return paired


def summarize(roots, confirmation, output):
    report = {
        "schema_version": "heritage-bounded-villages-evidence-1",
        "design": [],
        "confirmation": None,
        "new_CA_checks_in_summary": 0,
        "interpretation": "conditional_model_and_finite_scenarios;not_annual_risk_or_field_code",
    }
    for root in map(Path, roots):
        experiment, rows = checked_rows(root)
        result, audit = read_json(root / "search-result.json"), read_json(root / "audit.json")
        assert audit["audit_passed"] and audit["all_original_candidate_ids_preserved"]
        best = result["best_ids"]
        selected = [r for r in rows if r["demolished_ids"] in ([], best)]
        proof = (
            read_json(root / "proof-audit.json") if (root / "proof-audit.json").exists() else audit
        )
        report["design"].append(
            {
                "place_name": experiment["config"]["place_name"],
                "root": str(root.resolve()),
                "problem_sha256": experiment["problem_sha256"],
                "candidate_count": len(experiment["config"]["candidate_ids"]),
                "physical_checks": len(rows),
                "native_validation": audit["native_restoration_and_cut_check"],
                "best_ids": best,
                "bounds": result["bounds"],
                "optimality_proven": result["optimality_proven"],
                "minimum_count_proven": result["minimum_count_proven"],
                "certified_area_interval": proof.get("certified_area_interval"),
                "independent_count_certificate": proof.get("independent_minimum_count_certificate"),
                "layers": result["layers"],
                "baseline_and_best": pair_scales(selected),
                "all_checks": rows,
                "result_file_sha256": file_sha256(root / "search-result.json"),
            }
        )
    if confirmation is not None:
        root = Path(confirmation)
        experiment, rows = checked_rows(root)
        config = experiment["config"]
        result = read_json(root / "fixed-confirmation.json")
        assert result["complete"] and result["full_horizon_observation"]
        assert all(r["reached_horizon"] for r in rows)
        plan = config["frozen_plan_ids"]
        expected = {
            (tuple(ids), f"{fire}-h{scale:g}")
            for ids in ([], plan)
            for fire in experiment["problem"]["fires"]
            for scale in (1.0, 0.5)
        }
        assert {(tuple(r["demolished_ids"]), r["scenario_id"]) for r in rows} == expected
        assert len(rows) == len(expected) == config["max_total_checks"]
        design = next(
            d
            for d in report["design"]
            if d["best_ids"] == plan and d["place_name"] == config["place_name"]
        )
        assert design["result_file_sha256"] == config["parent_design_result_sha256"]
        report["confirmation"] = {
            "root": str(root.resolve()),
            "place_name": config["place_name"],
            "frozen_plan_ids": plan,
            "physical_checks": len(rows),
            "all_full_horizon": True,
            "new_source_audit": config["heldout_source_audit"],
            "no_candidate_reselection": result["no_candidate_reselection"],
            "paired": pair_scales(rows),
            "all_checks": rows,
        }
    write_json(output, report)
    print(
        {
            "design_villages": len(roots),
            "confirmation": confirmation is not None,
            "new_CA_checks": 0,
        }
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--roots", nargs="+", type=Path, required=True)
    parser.add_argument("--confirmation", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    summarize(args.roots, args.confirmation, args.output)
