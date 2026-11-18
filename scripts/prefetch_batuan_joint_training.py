"""Parallelize only already-requested design plans; never inspect held outcomes.

The deterministic search retains scenario ordering and first-failure screening.
Extra design evaluations may be cached but never change the declared prefix
score. File locks ensure at most one solve per scenario/plan cache signature.
"""

from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from run_batuan_building_pairs import dump
from run_batuan_joint_search import Oracle

from pyfireca.planning.joint import plan_from_mapping


def evaluate(task):
    output, sid, mapping = task
    oracle = Oracle(output)
    record = oracle.evaluate(sid, plan_from_mapping(mapping))
    return {
        "scenario_id": sid,
        "plan_id": record.intervention_id,
        "physical_solves": oracle.calls,
        "cache_hits": oracle.hits,
    }


def prefetch(output, workers):
    protocol = json.loads((output / "protocol.json").read_text())
    expected = [r["scenario_id"] for r in protocol["training"]]
    submitted = set()
    pending = {}
    completed = []
    with ProcessPoolExecutor(workers) as pool:
        while not (output / "selection.json").exists() or pending:
            for future, _key in list(pending.items()):
                if future.done():
                    completed.append(future.result())
                    del pending[future]
            if not (output / "selection.json").exists():
                groups = {}
                for path in (output / "cache").glob("*.json"):
                    record = json.loads(path.read_text())["record"]
                    if (
                        record["scenario_id"] not in expected
                        or not record["diagnostics"]["stop_at_first_structure_ignition"]
                    ):
                        continue
                    group = groups.setdefault(record["intervention_id"], {})
                    group[record["scenario_id"]] = record
                for plan_id, group in groups.items():
                    if any(r["metrics"]["ignited_structure_count"] > 0 for r in group.values()):
                        continue
                    plan = plan_from_mapping(next(iter(group.values()))["intervention"])
                    for sid in expected:
                        key = (plan_id, sid)
                        if sid in group or key in submitted:
                            continue
                        # Bound the queue, so newly discovered failures can prune work.
                        if len(pending) >= workers:
                            break
                        submitted.add(key)
                        future = pool.submit(evaluate, (str(output), sid, plan.to_mapping()))
                        pending[future] = key
            dump(
                output / "prefetch-ledger.json",
                {
                    "scope": "training only; already requested plans; ordered search unchanged",
                    "workers": workers,
                    "records": completed,
                    "pending": list(pending.values()),
                },
            )
            if not pending and (output / "selection.json").exists():
                break
            time.sleep(2)
    print(
        json.dumps(
            {
                "completed_prefetch_requests": len(completed),
                "physical_solves": sum(r["physical_solves"] for r in completed),
            }
        )
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    prefetch(args.output.resolve(), args.workers)
