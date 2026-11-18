"""Parallel full-grid checks after selection is frozen; identical main-study cache.

This changes scheduling only, not the frozen physical evaluator, scenarios,
selection, or cache identity. The main study independently consumes/checks each
result and exports its replays. No held-out result can feed search.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from time import sleep

from run_batuan_adaptive_search import TrainingOracle, digest, dump, save_checked_replay
from run_batuan_intervention_study import engine_for
from run_batuan_protection_search import context, scenarios_from

from pyfireca.planning.interventions.clearing import clearing_plan_from_mapping
from pyfireca.village.workflow_v3 import load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def check_one(study, label, raw):
    study = Path(study)
    protocol = json.loads((study / "protocol.json").read_text())
    base = load_v3_case_bundle(ROOT / "runs/batuan-slope-contact-20261016/case")
    near, types, annotation_sha = context(base)
    engine = engine_for(base, near, types, protocol["code"])
    if engine.case_sha256 != protocol["case_sha256"]:
        raise ValueError("full-grid parent case differs from the frozen protocol")
    if annotation_sha != protocol["annotated_structures_sha256"]:
        raise ValueError("structure annotations differ from the frozen protocol")
    scenarios = scenarios_from(protocol, "training") + scenarios_from(protocol, "held_out")
    oracle = TrainingOracle(
        engine,
        scenarios,
        scenarios,
        study / "full-cache",
        digest({"protocol": protocol, "stage": "full"}),
    )
    plan = clearing_plan_from_mapping(raw)
    # Hold the main oracle's exact plan lock through replay completion too.
    # The main job cannot rename a partially written replay while this worker
    # owns it; after a crash its normal interrupted-directory recovery applies.
    key = digest({"context": oracle.context, "plan": plan.to_mapping()})
    with (oracle.directory / (key + ".lock")).open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        records = oracle(plan)
        viewer = save_checked_replay(
            engine, scenarios[0], plan, study / "full" / plan.plan_id, records[0]
        )
    audit = {
        "label": label,
        "solver_calls": oracle.solver_calls,
        "cache_hits": oracle.cache_hits,
        "viewer": viewer,
        "replay_exported_under_plan_lock": True,
        "evaluation_signatures": [r.evaluation_sha256 for r in records],
    }
    dump(study / "full-prefetch" / (label + ".json"), audit)
    return audit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--wait-for-selection", action="store_true")
    args = parser.parse_args()
    study = args.study.resolve()
    selection = study / "selection.json"
    for _ in range(2160):  # bounded to six hours for this active job
        if selection.exists():
            break
        if not args.wait_for_selection:
            raise FileNotFoundError("freeze selection before validation")
        sleep(10)
    else:
        raise TimeoutError("selection was not frozen")
    frozen = json.loads(selection.read_text())
    if digest(frozen["plans"]) != frozen["sha256"]:
        raise ValueError("selection checksum mismatch")
    protocol = json.loads((study / "protocol.json").read_text())
    for name, sha in protocol["source_hashes"].items():
        if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != sha:
            raise ValueError("active evaluator is not the frozen source")
    source = Path(__file__)
    snapshot = study / "full-prefetch" / source.name
    snapshot.parent.mkdir(parents=True, exist_ok=True)
    snapshot.write_bytes(source.read_bytes())
    labels = ["baseline", *(z + "_" + f for z, f in frozen["best_by_zone"].items())]
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [
            pool.submit(check_one, str(study), label, frozen["plans"][label]) for label in labels
        ]
        results = [f.result() for f in futures]
    if json.loads(selection.read_text())["sha256"] != frozen["sha256"]:
        raise AssertionError("selection changed")
    dump(
        study / "full-prefetch" / "scheduling-audit.json",
        {
            "selection_sha256": frozen["sha256"],
            "workers": args.workers,
            "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "results": results,
            "changes_physics_or_selection": False,
        },
    )
    print("Full-grid validation cache ready", flush=True)


if __name__ == "__main__":
    main()
