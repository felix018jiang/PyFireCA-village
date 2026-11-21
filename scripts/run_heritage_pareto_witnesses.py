"""Replay two design cases per unresolved enclosure to bound its worst village losses."""

import argparse
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from resume_heritage_joint_schedule import (
    load_manager,
    read_json,
    sha256,
    verify_original,
    write_json,
)

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = ROOT / "runs/research/heritage-protected-union-pingyan-20261111"
SHARD = ROOT / "runs/research/heritage-pingyan-pareto-design-witnesses-20261111"
CONFIG = (
    ROOT
    / "examples/research/heritage/area-20261110"
    / "pingyan-protected-union-priority-longcap-comparison-20261111.json"
)
TABLE = ROOT / "docs/results/heritage-pingyan-three-policies-complete8-table-20261111.json"
MINIMUM = ROOT / "docs/results/heritage-pingyan-six-options-loss-capped-minimum-20261111.json"
REGISTRATION = "design-witness-registration.json"


def selected_cases():
    outcomes = read_json(TABLE)
    comparison = read_json(MINIMUM)
    bench = comparison["constraints"]
    candidates = [
        r["policy"]
        for r in comparison["observations"]
        if r["constraint_verdict"] == "loss_and_safety_unknown_price_already_higher"
    ]
    wide = outcomes["plan_order"].index("roof-seed-protected-union-ring-b0-w15.01")
    targets = {}
    for path in ORIGIN.glob("evaluations/*/complete.json"):
        targets[sha256(path)] = path
    area_cases, owner_cases = [], []
    for row in outcomes["paired_observations"]:
        measures = row["in_plan_order"][wide]
        manifest = targets[measures["manifest_sha256"]]
        check = manifest.parent / "check.json"
        if sha256(check) != read_json(manifest)["files_sha256"][check.name]:
            raise ValueError("original reference check changed")
        record = read_json(check)
        if record["evidence"]["scale_m"] != 1.0:
            continue
        if measures["A_burned_m2"] == bench["maximum_burned_out_covered_area_m2"]:
            area_cases.append(record["scenario_id"])
        if (
            measures["N_original_roof_owner_bounds"]["upper"]
            == bench["maximum_original_owner_N_upper"]
        ):
            owner_cases.append(record["scenario_id"])
    if (
        len(area_cases) != 1
        or len(owner_cases) != 1
        or area_cases == owner_cases
        or len(candidates) != 2
    ):
        raise ValueError(
            "the two original coarse design witnesses or unresolved candidates changed"
        )
    return [(p, s) for p in candidates for s in area_cases + owner_cases]


def parent_state():
    budget = read_json(ORIGIN / "execution-budget.json")
    if budget["active"]:
        status = subprocess.run(
            ["ps", "-p", str(budget["active"]["pid"]), "-o", "stat="],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
        if not status.startswith("T"):
            raise RuntimeError(
                "the original catalogue worker must stay parked during the separate allocation"
            )
    return budget


def register():
    exp = verify_original(ORIGIN, CONFIG)
    budget = parent_state()
    selection = selected_cases()
    if budget["started_checks"] + len(selection) > exp["config"]["max_new_checks"]:
        raise ValueError("combined allocation exceeds the original case budget")
    manager = load_manager(ORIGIN)
    eligible = {
        (p.plan_id, s): manager.result_folder(ORIGIN, p, s)
        for p, s in manager.execution_checks(exp["config"])
    }
    if any(eligible[pair].exists() for pair in selection):
        raise ValueError("requested witness already exists")
    if SHARD.exists():
        raise FileExistsError(SHARD)
    with tempfile.TemporaryDirectory(dir=SHARD.parent) as working:
        output = Path(working) / "run"
        output.mkdir()
        for name in ("experiment.json", "preflight.json"):
            os.link(ORIGIN / name, output / name)
        shutil.copytree(
            ORIGIN / "source-snapshot", output / "source-snapshot", copy_function=os.link
        )
        write_json(
            output / "execution-budget.json", {"active": None, "elapsed_s": 0, "started_checks": 0}
        )
        write_json(
            output / REGISTRATION,
            dict(
                experiment_sha256=sha256(ORIGIN / "experiment.json"),
                preflight_sha256=sha256(ORIGIN / "preflight.json"),
                table_sha256=sha256(TABLE),
                candidate_comparison_sha256=sha256(MINIMUM),
                runner_sha256=sha256(__file__),
                native_loader_sha256=sha256(
                    Path(__file__).with_name("resume_heritage_joint_schedule.py")
                ),
                original_budget=budget,
                slots=len(selection),
                selected_cases=selection,
                selection_rule=(
                    "All unresolved local enclosures, on each coarse design witness "
                    "attaining the reduced plan's worst A or N upper. No holdout used."
                ),
                exclusion_rule=(
                    "Any strict valid heritage failure excludes feasibility. Under "
                    "worst-case A and N upper, separate valid cases can prove both lower "
                    "bounds. Greater C establishes dominance even if the other outcomes are "
                    "unknown."
                ),
                new_independent_fires=0,
                imported_outcomes=0,
            ),
        )
        output.rename(SHARD)


def resume():
    exp = verify_original(SHARD, CONFIG)
    r = read_json(SHARD / REGISTRATION)
    if (
        sha256(__file__) != r["runner_sha256"]
        or sha256(Path(__file__).with_name("resume_heritage_joint_schedule.py"))
        != r["native_loader_sha256"]
        or r["original_budget"] != parent_state()
        or r["experiment_sha256"] != sha256(SHARD / "experiment.json")
        or r["preflight_sha256"] != sha256(SHARD / "preflight.json")
        or r["table_sha256"] != sha256(TABLE)
        or r["candidate_comparison_sha256"] != sha256(MINIMUM)
        or r["selected_cases"] != list(map(list, selected_cases()))
    ):
        raise ValueError("original evidence, allocation or runner changed")
    manager = load_manager(SHARD)
    cases = {(p.plan_id, s): (p, s) for p, s in manager.execution_checks(exp["config"])}

    def scheduled(settings):
        for key in r["selected_cases"]:
            plan, scenario = cases[tuple(key)]
            spent = read_json(SHARD / "execution-budget.json")
            if parent_state()["elapsed_s"] + spent["elapsed_s"] >= settings["max_wall_time_s"]:
                return
            if spent["started_checks"] >= r["slots"]:
                return
            yield plan, scenario

    manager.execution_checks = scheduled
    manager.resume(CONFIG, SHARD)


if __name__ == "__main__":
    cli = argparse.ArgumentParser(description=__doc__)
    cli.add_argument("mode", choices=("register", "resume"))
    register() if cli.parse_args().mode == "register" else resume()
