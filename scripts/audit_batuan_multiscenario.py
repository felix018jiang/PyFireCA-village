"""Audit source/input locks, cached evaluations, certificates and frozen choices."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
from run_batuan_adaptive_search import digest, dump
from run_batuan_multiscenario_search import ScenarioOracle, file_sha, scenario, verify_sources

from pyfireca.planning.evaluation import EvaluationResult
from pyfireca.planning.interventions.clearing import clearing_plan_from_mapping
from pyfireca.planning.robust import ProtectionScreen, protection_failed


def audit(study):
    study = Path(study)
    protocol = json.loads((study / "protocol.json").read_text())
    verify_sources(protocol)
    for name, sha in protocol["source_hashes"].items():
        if file_sha(study / "source_snapshot" / name) != sha:
            raise ValueError("frozen source snapshot changed")
    selection = json.loads((study / "selection.json").read_text())
    if digest(protocol) != selection["protocol_sha256"] or selection["held_out_results_read"]:
        raise ValueError("selection/protocol isolation failed")
    report = json.loads((study / "report.json").read_text())
    if digest(selection) != report["selection_sha256"]:
        raise ValueError("published selection changed")
    training_ids = tuple(r["scenario_id"] for r in protocol["training"])
    held_ids = tuple(r["scenario_id"] for r in protocol["held_out"])
    if len(training_ids) != 24 or len(held_ids) != 8 or set(training_ids) & set(held_ids):
        raise ValueError("design/validation suite invalid")
    counts = {
        "source_files": len(protocol["source_hashes"]),
        "input_files": len(protocol["input_hashes"]),
        "physical_records": 0,
        "full_physical_records": 0,
        "search_requests": 0,
        "certified_search_records": 0,
        "rejected_requests": 0,
        "partial_failure_records": 0,
    }
    for full in (False, True):
        cache = study / ("full-cache" if full else "evaluation-cache")
        if not cache.exists():
            continue
        oracle = ScenarioOracle(study, full=full)
        for path in sorted(cache.glob("*.json")):
            raw = json.loads(path.read_text())
            r = EvaluationResult(**raw["record"])
            if raw["record_sha256"] != digest(raw["record"]):
                raise ValueError("cache payload checksum failed")
            early = r.diagnostics["stop_at_first_structure_ignition"]
            # Cache must serve the exact existing key, never invoke the CA.
            engine = oracle.engine(oracle.rows[r.scenario_id]["environment_id"])

            def forbidden(*_args, **_kwargs):
                raise AssertionError("audit attempted a fresh physical simulation")

            engine.evaluate = forbidden
            checked = oracle.evaluate(
                r.scenario_id, clearing_plan_from_mapping(r.intervention), complete=not early
            )
            if checked.evaluation_sha256 != path.stem or checked.to_mapping() != raw["record"]:
                raise ValueError("cached evaluation identity changed")
            if not r.diagnostics["parent_case_unchanged"]:
                raise ValueError("source model was mutated")
            if not r.diagnostics["reached_requested_horizon"] and not protection_failed(r):
                raise ValueError("zero-loss early prefix was treated as complete")
            if r.diagnostics["simulated_until_s"] > r.horizon_s + 1e-8:
                raise ValueError("evaluation exceeded requested horizon")
            errors = r.diagnostics["energy_closure_errors_J"]
            if max(errors.values()) > r.diagnostics["energy_tolerance_J"]:
                raise ValueError("energy budget failed")
            counts["full_physical_records" if full else "physical_records"] += 1
            if not r.diagnostics["reached_requested_horizon"]:
                counts["partial_failure_records"] += 1
        if oracle.calls:
            raise ValueError("audit must not compute fresh outcomes")
    pruning_oracle = ScenarioOracle(study)
    for path in study.glob("*-search.json"):
        result = json.loads(path.read_text())
        by_id = {r["plan_id"]: r for r in result["records"]}
        for plan_id, bound in result.get("area_bound_pruned", {}).items():
            row = by_id[plan_id]
            incumbent = by_id[bound["incumbent_plan_id"]]
            if not incumbent["protection_screen"]["protection_goal_met"]:
                raise ValueError("area bound has no complete protection certificate")
            if incumbent["query_index"] >= row["query_index"]:
                raise ValueError("area pruning used a future outcome")
            plan = clearing_plan_from_mapping(row["plan"])
            first = protocol["training"][0]
            engine = pruning_oracle.engine(first["environment_id"])
            _, _, treatment = engine.prepare(scenario(first), plan)
            if abs(treatment["cleared_area_m2"] - bound["cleared_area_m2"]) > 1e-7:
                raise ValueError("pruned area is not exact eligible clearance")
            if treatment["cleared_area_m2"] < incumbent["cleared_area_m2"] - 1e-8:
                raise ValueError("area pruning incorrectly removed an improving candidate")
        for row in result["records"]:
            counts["search_requests"] += 1
            if row["status"] == "rejected":
                counts["rejected_requests"] += 1
                continue
            summary = row["protection_screen"]
            if tuple(summary["expected_scenario_ids"]) != training_ids:
                raise ValueError("search accessed an unexpected scenario set")
            # Find physical records by their immutable signatures.
            records = []
            for sha in summary["evaluation_sha256"]:
                record = json.loads((study / "evaluation-cache" / (sha + ".json")).read_text())[
                    "record"
                ]
                if record["intervention_id"] != row["plan_id"]:
                    raise ValueError("search record refers to a different plan")
                records.append(EvaluationResult(**record))
            screened = ProtectionScreen(training_ids, tuple(records))
            if digest(screened.to_mapping()) != digest(summary):
                raise ValueError("search protection certificate changed")
            if (row["effectiveness"][0] == 0) != screened.passed:
                raise ValueError("zero objective does not equal full-suite protection")
            if screened.passed:
                counts["certified_search_records"] += 1
    for zone, selected in selection["chosen"].items():
        records = [
            r
            for path in study.glob(zone + "-replica*-search.json")
            for r in json.loads(path.read_text())["records"]
            if r["status"] == "ok"
        ]
        passed = [r for r in records if r["protection_screen"]["protection_goal_met"]]
        if bool(passed) != selected["training_goal_met"]:
            raise ValueError("selection protection flag invalid")
        if (
            passed
            and abs(min(r["cleared_area_m2"] for r in passed) - selected["cleared_area_m2"]) > 1e-7
        ):
            raise ValueError("selection did not choose minimum observed feasible area")
        if selected["plan"] not in [r["plan"] for r in records]:
            raise ValueError("selection was not actually evaluated")
    for groups in report["evaluations"].values():
        for suite, ids in (("training", training_ids), ("held_out", held_ids)):
            rows = groups[suite]["records"]
            screen = ProtectionScreen(ids, tuple(EvaluationResult(**r) for r in rows))
            if not screen.complete or digest(screen.to_mapping()) != digest(
                groups[suite]["screen"]
            ):
                raise ValueError("published comparison is incomplete")
            if any(not r["diagnostics"]["reached_requested_horizon"] for r in rows):
                raise ValueError("published 90-minute totals contain a stopped run")
    replay_count = 0
    if (study / "replays.json").exists():
        oracle = ScenarioOracle(study, full=True)
        for row in json.loads((study / "replays.json").read_text()):
            sha = row["evaluation_sha256"]
            r = json.loads((study / "full-cache" / (sha + ".json")).read_text())["record"]
            root = study / "replays" / row["label"]
            times = np.load(root / "run/outputs/ignition_time_s.npy")
            if int(np.isfinite(times).sum()) != r["metrics"]["ignited_cells"]:
                raise ValueError("replay final state mismatch")
            replay_count += 1
    result = {
        "status": "passed",
        "counts": counts,
        "replays_checked": replay_count,
        "selection_sha256": digest(selection),
        "protocol_sha256": digest(protocol),
        "report_file_sha256": file_sha(study / "report.json"),
        "fresh_physical_solves_during_audit": 0,
        "full_validation": json.loads((study / "full-validation.json").read_text())
        if (study / "full-validation.json").exists()
        else None,
    }
    dump(study / "audit.json", result)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    print(json.dumps(audit(parser.parse_args().study), ensure_ascii=False, indent=2))
