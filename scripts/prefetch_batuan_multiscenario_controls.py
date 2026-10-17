"""Complete design-only historical controls while the constrained search runs."""

import argparse
import json
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

from run_batuan_multiscenario_search import final_task

from pyfireca.planning.adaptive import canonical_plan


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
            pool.submit(final_task, str(args.study), label, plan, "training")
            for label, plan in plans.items()
        ]
        for task in tasks:
            print(task.result(), flush=True)


if __name__ == "__main__":
    main()
