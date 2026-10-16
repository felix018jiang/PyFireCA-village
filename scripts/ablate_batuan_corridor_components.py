"""Post-selection component ablation; no feedback to the frozen main search."""

from __future__ import annotations

import argparse
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from run_batuan_adaptive_search import digest, dump
from run_batuan_intervention_study import engine_for
from run_batuan_protection_search import LockedOracle, context, scenarios_from

from pyfireca.planning.adaptive import canonical_plan
from pyfireca.planning.interventions.clearing import clearing_plan_from_mapping
from pyfireca.planning.sensitivity import ignition_rise_sensitivity
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def evaluate_component(study, index):
    study = Path(study)
    protocol = json.loads((study / "protocol.json").read_text())
    ablation = json.loads((study / "component-ablation-protocol.json").read_text())
    bundle = load_v3_case_bundle(study / "search-case")
    near, types, _ = context(bundle)
    scenarios = scenarios_from(protocol, "training") + scenarios_from(protocol, "held_out")
    combined = clearing_plan_from_mapping(ablation["combined_plan"])
    plan = canonical_plan(combined.zone_id, (combined.paths[index],))
    result = {"path_index": index, "plan": plan.to_mapping(), "base": [], "sensitivity": []}
    for profile, case in (
        ("base", bundle),
        ("sensitivity", ignition_rise_sensitivity(bundle, 0.95)),
    ):
        engine = engine_for(case, near, types, protocol["code"])
        oracle = LockedOracle(
            engine,
            scenarios,
            scenarios,
            study / "component-ablation-cache",
            digest({"protocol": ablation, "profile": profile}),
        )
        result[profile] = [r.to_mapping() for r in oracle(plan)]
    dump(study / f"component-{index}-ablation.json", result)
    return result


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("study", type=Path)
    args = p.parse_args()
    study = args.study.resolve()
    report = json.loads((study / "report.json").read_text())
    frozen = json.loads((study / "selection.json").read_text())
    if report["status"] != "completed" or digest(report["chosen_plans"]) != frozen["sha256"]:
        raise ValueError("complete and freeze the main study first")
    for name, sha in report["protocol"]["source_hashes"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha:
            raise ValueError("physical source changed")
    combined = report["chosen_plans"]["outer_proxy_multiple"]
    if len(combined["paths"]) != 2:
        raise ValueError("this preregistered ablation expects two paths")
    protocol = {
        "schema": "batuan-component-removal-ablation-1",
        "role": "post-selection mechanism study, not independent model selection",
        "main_selection_sha256": frozen["sha256"],
        "physical_code": report["protocol"]["code"],
        "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "combined_plan": combined,
        "path_indices": [0, 1],
        "scenario_ids": [
            s["scenario_id"]
            for s in report["protocol"]["training"] + report["protocol"]["held_out"]
        ],
        "rise_factors": [1, 0.95],
        "horizon_s": 5400,
        "selection_feedback": False,
    }
    target = study / "component-ablation-protocol.json"
    if target.exists() and digest(json.loads(target.read_text())) != digest(protocol):
        raise ValueError("ablation protocol changed")
    dump(target, protocol)
    with ProcessPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(evaluate_component, str(study), i) for i in (0, 1)]
        results = [f.result() for f in futures]
    if json.loads((study / "selection.json").read_text())["sha256"] != frozen["sha256"]:
        raise AssertionError("ablation changed selection")
    dump(
        study / "component-ablation.json",
        {
            "protocol": protocol,
            "components": results,
            "combined_reference": {
                "base": report["controls"]["outer_proxy_multiple"]
                + report["validation"]["outer_proxy_multiple"],
                "sensitivity": report["sensitivity"]["outer_proxy_multiple"],
            },
        },
    )
    snapshot = study / "component-ablation-source" / Path(__file__).name
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(Path(__file__).read_bytes())
    print("Component ablation complete", flush=True)


if __name__ == "__main__":
    main()
