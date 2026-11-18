"""Complete design-only historical controls while the constrained search runs."""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from run_batuan_adaptive_search import dump
from run_batuan_multiscenario_search import ScenarioOracle

from pyfireca.planning.adaptive import canonical_plan
from pyfireca.planning.interventions.clearing import clearing_plan_from_mapping
from pyfireca.planning.robust import screen_protection


def control_task(study, label, raw):
    oracle = ScenarioOracle(study)
    plan = clearing_plan_from_mapping(raw)
    oracle.preflight(plan)
    ids = [r["scenario_id"] for r in oracle.protocol["training"]]
    # Do not call oracle.screen: its design-search ledger is reserved for the
    # exact first-failure prefix used by the optimizer until selection freezes.
    result = screen_protection(
        ids, lambda sid: oracle.evaluate(sid, plan, complete=True), stop_at_failure=False
    )
    dump(
        Path(study) / "final-evaluations" / (label + "-training.json"),
        {
            "label": label,
            "suite": "training",
            "screen": result.to_mapping(),
            "records": [r.to_mapping() for r in result.records],
        },
    )
    return label, "training"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("study", type=Path)
    args = parser.parse_args()
    protocol = json.loads((args.study / "protocol.json").read_text())
    plans = {
        "baseline": canonical_plan("outer_proxy", ()).to_mapping(),
        **{"previous_" + z: p for z, p in protocol["previous_controls"].items()},
    }
    with ProcessPoolExecutor(max_workers=2) as pool:
        tasks = [
            pool.submit(control_task, str(args.study), label, plan) for label, plan in plans.items()
        ]
        for task in tasks:
            print(task.result(), flush=True)


if __name__ == "__main__":
    main()
