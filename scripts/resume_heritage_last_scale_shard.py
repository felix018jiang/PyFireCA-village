"""Resume only the parallel shard's originally registered native case."""

import hashlib
import json
import sys
from pathlib import Path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def resume():
    root = Path(__file__).resolve().parents[1]
    run = root / "runs/research/heritage-pingyan-observed-clearance-last-scale-shard-20261111"
    config = (
        root
        / "examples/research/heritage/area-20261110/pingyan-observed-clearance-subset-20261111.json"
    )
    registration = json.loads((run / "parallel-execution-registration.json").read_text())
    experiment = json.loads((run / "experiment.json").read_text())
    if (
        sha(run / "experiment.json") != registration["native_experiment_sha256"]
        or sha(run / "preflight.json") != registration["original_preflight_sha256"]
        or sha(config) != experiment["config_sha256"]
        or registration["schedule_slots"] != 1
    ):
        raise ValueError("native shard registration changed")
    budget = json.loads((run / "execution-budget.json").read_text())
    if budget.get("active") is not None:
        raise RuntimeError("the recorded worker must be identified before resuming")
    if list(run.glob("evaluations/*/complete.json")):
        return
    if budget["started_checks"] >= registration["schedule_slots"]:
        raise RuntimeError("the charged slot requires a documented recovery allocation")
    snapshot = run / "source-snapshot"
    if any(sha(snapshot / name) != h for name, h in experiment["source_sha256"].items()):
        raise ValueError("frozen native sources changed")
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(snapshot / "scripts"), str(snapshot / "src")]
    import run_heritage_joint_comparison as native

    original = native.execution_checks

    def selected(settings):
        rows = [
            (plan, scenario)
            for plan, scenario in original(settings)
            if plan.plan_id == registration["registered_plan"]
            and scenario == registration["registered_case"]
        ]
        if len(rows) != 1:
            raise ValueError("parallel case must belong to the unchanged native question")
        yield from rows

    native.execution_checks = selected
    native.resume(config, run)


if __name__ == "__main__":
    resume()
