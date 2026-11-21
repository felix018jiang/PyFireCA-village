"""Speculate on registered design scenarios without changing search decisions.

Only candidates already evaluated safely on the first design scenario qualify.
Workers fill the same locked physical cache; no screening ledger or selection
is written. Reverse-order speculation avoids the main oracle's next scenario.
The main oracle still consumes its declared prefix in order, including failures.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from run_batuan_adaptive_search import digest, dump
from run_batuan_multiscenario_search import ScenarioOracle

from pyfireca.planning.interventions.clearing import clearing_plan_from_mapping

ORACLE = None


def initialize(study):
    global ORACLE
    ORACLE = ScenarioOracle(study)


def evaluate(raw, sid):
    plan = clearing_plan_from_mapping(raw)
    ORACLE.preflight(plan)
    record = ORACLE.evaluate(sid, plan, complete=False)
    return {
        "plan_id": plan.plan_id,
        "scenario_id": sid,
        "failed": record.metrics["ignited_house_count"] + record.metrics["ignited_bridge_count"]
        > 0,
        "evaluation_sha256": record.evaluation_sha256,
    }


def pending(study, ids):
    groups = {}
    for path in (study / "evaluation-cache").glob("*.json"):
        raw = json.loads(path.read_text())
        record = raw["record"]
        if not record["diagnostics"]["stop_at_first_structure_ignition"]:
            continue
        if record["scenario_id"] not in ids:
            continue
        if digest(record) != raw["record_sha256"]:
            raise ValueError("prefetch encountered a corrupt physical record")
        groups.setdefault(record["intervention_id"], {})[record["scenario_id"]] = record
    qualified, upper = [], {}
    for rows in groups.values():
        if ids[0] not in rows:
            continue
        if any(
            r["metrics"]["ignited_house_count"] + r["metrics"]["ignited_bridge_count"]
            or not r["diagnostics"]["reached_requested_horizon"]
            for r in rows.values()
        ):
            continue
        first = rows[ids[0]]
        plan = first["intervention"]
        area, zone = plan["cleared_area_m2"], plan["zone_id"]
        if len(rows) == len(ids):
            upper[zone] = min(upper.get(zone, float("inf")), area)
        else:
            qualified.append(
                (len(rows), area, plan, [sid for sid in reversed(ids) if sid not in rows])
            )
    return [
        row
        for row in sorted(qualified, key=lambda x: (-x[0], x[1], x[2]["plan_id"]))
        if row[1] < upper.get(row[2]["zone_id"], float("inf")) - 1e-8
    ]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("workers must be positive")
    study = args.study.resolve()
    protocol = json.loads((study / "protocol.json").read_text())
    ids = [row["scenario_id"] for row in protocol["training"]]
    if len(ids) != 24 or len(set(ids)) != 24:
        raise ValueError("this helper requires the registered 24-scenario design suite")
    dump(
        study / "design-prefetch-protocol.json",
        {
            "role": "design_only_speculation_never_changes_prefix_order_or_selection",
            "protocol_sha256": digest(protocol),
            "workers": args.workers,
            "scenario_ids": ids,
            "held_out_access": False,
            "source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        },
    )
    futures = {}
    submitted = completed = 0
    with (
        (study / "design-prefetch-history.jsonl").open("a") as log,
        ProcessPoolExecutor(
            max_workers=args.workers, initializer=initialize, initargs=(str(study),)
        ) as pool,
    ):
        while not (study / "selection.json").exists():
            for future in list(futures):
                if future.done():
                    record = future.result()
                    log.write(json.dumps({"event": "completed", **record}) + "\n")
                    log.flush()
                    completed += 1
                    del futures[future]
            busy = set(futures.values())
            slots = args.workers - len(futures)
            if slots:
                for _count, _area, raw, missing in pending(study, ids):
                    for sid in missing:
                        identity = (raw["plan_id"], sid)
                        if identity in busy:
                            continue
                        future = pool.submit(evaluate, raw, sid)
                        futures[future] = identity
                        busy.add(identity)
                        submitted += 1
                        slots -= 1
                        log.write(
                            json.dumps(
                                {
                                    "event": "submitted",
                                    "plan_id": identity[0],
                                    "scenario_id": sid,
                                }
                            )
                            + "\n"
                        )
                        log.flush()
                        if not slots:
                            break
                    if not slots:
                        break
            time.sleep(5)
        for future in futures:
            record = future.result()
            completed += 1
            log.write(json.dumps({"event": "completed", **record}) + "\n")
    dump(
        study / "design-prefetch-completion.json",
        {
            "submitted": submitted,
            "completed": completed,
            "held_out_evaluations": 0,
            "selection_untouched": True,
        },
    )


if __name__ == "__main__":
    main()
