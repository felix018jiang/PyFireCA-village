"""First joint vegetation-corridor pilot: one model, immutable material baseline.

Outer/inner remain explicit 20 m proxies. No field moisture inputs, timber
species inference, wall opacity, demolition or firebrand development is added.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from run_batuan_building_pairs import dump, sha
from run_batuan_intervention_study import zones_for
from run_batuan_protection_search import context

from pyfireca.planning.adaptive import (
    CorridorSearchConfig,
    SearchRejected,
    canonical_plan,
    search_corridors,
)
from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.environments import ProtectionEnvironment, apply_environment
from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario
from pyfireca.planning.interventions.clearing import clearing_footprint, clearing_plan_from_mapping
from pyfireca.planning.joint import (
    JointClearingPlan,
    JointVillageEvaluationEngine,
    combined_seed,
    joint_initial_plans,
    optimize_joint_corridors,
    plan_from_mapping,
)
from pyfireca.planning.robust import screen_protection
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import crop_v3_case_bundle, load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runs/batuan-slope-contact-20261016/case"
OLD = ROOT / "runs/research/batuan-multiscenario-20261017-r4/selection.json"
MODES = ("outer_proxy", "inner_proxy", "joint")
WINDOW = (90, 230, 80, 220)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def prepare(output, budget):
    output.mkdir(parents=True, exist_ok=False)
    parent = load_v3_case_bundle(BASE)
    if parent.request.firebrands is not None:
        raise ValueError("pilot requires stable nonfirebrand baseline")
    old = json.loads(OLD.read_text())["chosen"]
    plans = {k: clearing_plan_from_mapping(v["plan"]) for k, v in old.items()}
    envs = {
        name: asdict(ProtectionEnvironment(name, 1.0, speed, direction, 0.2))
        for name, speed, direction in [
            ("sw4", 4, 225),
            ("se2", 2, 135),
            ("nw3", 3, 315),
            ("ne3", 3, 45),
        ]
    }

    def suite(cells, names, role):
        rows = []
        for env in names:
            for r, c in cells:
                if (
                    not parent.request.burnable_mask[r, c]
                    or parent.request.building_index[r, c] >= 0
                ):
                    raise ValueError(f"nonvegetation source {r},{c}")
                rows.append(
                    {
                        "scenario_id": f"{role}-r{r}c{c}-{env}",
                        "environment_id": env,
                        "parent_cell": [r, c],
                        "point_m": parent.request.grid.center(r, c),
                    }
                )
        return rows

    train = suite([(163, 151), (160, 151), (168, 143), (170, 159)], ["sw4", "se2"], "design")
    # New source/weather combinations, never included in search evaluation.
    held = suite([(172, 147), (173, 153)], ["nw3", "ne3"], "held")
    sources = sorted(
        [
            *(ROOT / "src/pyfireca/planning").rglob("*.py"),
            *(ROOT / "src/pyfireca/village").glob("*.py"),
            Path(__file__),
            ROOT / "scripts/run_batuan_building_pairs.py",
            ROOT / "scripts/run_batuan_intervention_study.py",
            ROOT / "scripts/run_batuan_protection_search.py",
            ROOT / "scripts/run_batuan_adaptive_search.py",
        ]
    )
    r0, r1, c0, c1 = WINDOW
    crop_v3_case_bundle(
        BASE,
        output / "search-case",
        row_start=r0,
        row_stop=r1,
        col_start=c0,
        col_stop=c1,
        end_time_s=5400,
        sample_interval_s=60,
    )
    cropped = load_v3_case_bundle(output / "search-case")
    _near, types, annotation_hash = context(parent)
    protocol = {
        "schema": "batuan-joint-scoped-pilot-2",
        "training": train,
        "held_out": held,
        "environments": envs,
        "horizon_s": 5400,
        "window": WINDOW,
        "base_fingerprint": request_fingerprint(parent.request),
        "crop_fingerprint": request_fingerprint(cropped.request),
        "types": types,
        "annotation_sha256": annotation_hash,
        "historical_plans": {k: v.to_mapping() for k, v in plans.items()},
        "source_hashes": {str(p.relative_to(ROOT)): sha(p) for p in sources},
        "input_hashes": {
            str(p): sha(p)
            for d in (BASE, output / "search-case")
            for p in d.glob("*")
            if p.is_file()
        },
        "seed": 20261017,
        "candidate_budget_per_mode": budget,
        "objective": (
            "all 8 design scenarios zero house and bridge ignition, then minimum union cleared area"
        ),
        "material_policy": (
            "fixed equivalent timber and water; no threshold reduction or species inference"
        ),
        "scope": (
            "bounded shared-physics clearing prototype; not field construction or global optimum"
        ),
        "selection_rule": "freeze train-only winners; held outcomes never used for reselection",
        "max_paths": 3,
        "max_vertices": 5,
        "independent_combination": "seven initial allocation plans, same physical scenarios",
        "domain_policy": "20m proxies; each component retains its original region mask; union once",
        "joint_method": (
            "seven allocation seeds, conditional regional geometry search; "
            "single-layer limits retained"
        ),
    }
    for name in protocol["source_hashes"]:
        dest = output / "source_snapshot" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    dump(output / "protocol.json", protocol)
    print(json.dumps({"prepared": str(output), "train": len(train), "held": len(held)}), flush=True)


class Oracle:
    def __init__(self, output, full=False):
        self.output = Path(output)
        self.protocol = json.loads((self.output / "protocol.json").read_text())
        for name, h in self.protocol["source_hashes"].items():
            if sha(ROOT / name) != h:
                raise ValueError(f"frozen source changed: {name}")
        for name, h in self.protocol["input_hashes"].items():
            if sha(name) != h:
                raise ValueError(f"frozen input changed: {name}")
        self.bundle = load_v3_case_bundle(BASE if full else self.output / "search-case")
        if (
            request_fingerprint(self.bundle.request)
            != self.protocol["base_fingerprint" if full else "crop_fingerprint"]
        ):
            raise ValueError("base fingerprint mismatch")
        self.full = full
        self.engines = {}
        self.calls = 0
        self.hits = 0
        self.rows = {
            s["scenario_id"]: s for s in self.protocol["training"] + self.protocol["held_out"]
        }
        self.cache = self.output / ("full-cache" if full else "cache")
        self.cache.mkdir(exist_ok=True)
        self.model_identity = digest(self.protocol["source_hashes"])
        self.near, self.types, annotation_hash = context(self.bundle)
        if annotation_hash != self.protocol["annotation_sha256"]:
            raise ValueError("frozen building annotations changed")

    def engine(self, eid):
        if eid not in self.engines:
            b = apply_environment(
                self.bundle, ProtectionEnvironment(**self.protocol["environments"][eid])
            )
            zones = zones_for(b.request.grid, self.near)
            self.engines[eid] = JointVillageEvaluationEngine(
                b,
                V3RadiativeTransportParameters(**b.metadata["transport_parameters"]),
                outer_mask=zones["outer_proxy"],
                inner_mask=zones["inner_proxy"],
                structure_types=self.types,
                code_version=self.model_identity,
            )
        return self.engines[eid]

    def preflight(self, plan):
        # Only source coordinates, not held results, are used to preserve inputs.
        from shapely.geometry import box
        from shapely.ops import unary_union

        footprint = (
            unary_union([clearing_footprint(p) for p in plan.components])
            if isinstance(plan, JointClearingPlan)
            else clearing_footprint(plan)
        )
        h = self.bundle.request.grid.cell_size_m / 2
        for raw in self.rows.values():
            x, y = raw["point_m"]
            if footprint.intersection(box(x - h, y - h, x + h, y + h)).area > 1e-8:
                raise SearchRejected("candidate overlaps a protected fire-source cell")

    def evaluate(self, sid, plan, complete=False):
        self.preflight(plan)
        raw = self.rows[sid]
        e = self.engine(raw["environment_id"])
        s = EvaluationScenario(sid, (Ignition(tuple(raw["point_m"])),), 5400)
        signature = digest(
            {
                "case": e.case_sha256,
                "code": e.code_version,
                "plan": plan.to_mapping(),
                "scenario": asdict(s),
                "transport": asdict(e.transport),
                "complete": complete,
            }
        )
        path = self.cache / (signature + ".json")
        with (self.cache / (signature + ".lock")).open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            if path.exists():
                row = json.loads(path.read_text())
                if row["signature"] != signature or digest(row["record"]) != row["record_sha256"]:
                    raise ValueError("cache checksum or signature mismatch")
                record = EvaluationResult(**row["record"])
                self.hits += 1
            else:
                record = e.evaluate(s, plan, stop_at_first_structure_ignition=not complete)
                self.calls += 1
                dump(
                    path,
                    {
                        "signature": signature,
                        "record": record.to_mapping(),
                        "record_sha256": digest(record.to_mapping()),
                    },
                )
        if record.scenario_id != sid or record.intervention_id != plan.plan_id:
            raise ValueError("cache scenario/plan mismatch")
        return record

    def screen(self, plan, role="training", complete=False):
        return screen_protection(
            [r["scenario_id"] for r in self.protocol[role]],
            lambda sid: self.evaluate(sid, plan, complete),
            stop_at_failure=not complete,
        )

    def __call__(self, plan):
        try:
            return list(self.screen(plan).records)
        except ValueError as error:
            if "domain-edge" in str(error):
                raise SearchRejected(str(error)) from error
            raise


def search_mode(task):
    output, mode = task
    o = Oracle(output)
    p = o.protocol
    e = o.engine("sw4")
    old = {k: clearing_plan_from_mapping(v) for k, v in p["historical_plans"].items()}
    config = CorridorSearchConfig(
        seed=p["seed"],
        max_evaluations=p["candidate_budget_per_mode"],
        objective="joint_protection_then_area",
        initial_step_m=10,
        max_paths=3,
        max_vertices=5,
    )
    events = []

    def progress(event):
        events.append(event)
        dump(Path(output) / (mode + "-progress.json"), event)
        if event["event"] == "evaluation":
            print(
                json.dumps(
                    {"mode": mode, "query": event["query_index"], "status": event["status"]}
                ),
                flush=True,
            )

    if mode == "joint":
        seeds = joint_initial_plans(old["outer_proxy"], old["inner_proxy"])
        result = optimize_joint_corridors(
            engine=e, seeds=seeds, evaluate=o, config=config, progress=progress
        )
    else:
        base = old[mode]
        seeds = [
            canonical_plan(
                mode,
                tuple(replace(path, width_m=max(2.5, path.width_m * scale)) for path in base.paths),
            )
            for scale in (1, 0.5, 0.75, 1.25)
        ]
        grid = e.bundle.request.grid
        allowed = e.zones[mode] & grid_mask(e)
        result = search_corridors(
            zone_id=mode,
            bounds=(
                grid.x_min_m,
                grid.y_max_m - grid.height * grid.cell_size_m,
                grid.x_min_m + grid.width * grid.cell_size_m,
                grid.y_max_m,
            ),
            anchors=tuple(grid.center(int(r), int(c)) for r, c in np.argwhere(allowed)),
            seeds=seeds,
            evaluate=o,
            config=config,
            progress=progress,
        )
    valid = [r for r in result["records"] if r["status"] == "ok" and r["effectiveness"][0] == 0]
    selected = min(valid, key=lambda r: (r["cleared_area_m2"], r["plan_id"])) if valid else None
    result.update(mode=mode, selected=selected, physical_solves=o.calls, cache_hits=o.hits)
    dump(Path(output) / (mode + "-search.json"), result)
    return mode, selected


def grid_mask(engine):
    r = engine.bundle.request
    return r.burnable_mask & (r.building_index < 0)


def search(output, workers):
    with ProcessPoolExecutor(workers) as pool:
        selected = dict(pool.map(search_mode, [(str(output), m) for m in MODES]))
    # Independent historical combination is given the same prescribed allocation sweep.
    o = Oracle(output)
    old = {k: clearing_plan_from_mapping(v) for k, v in o.protocol["historical_plans"].items()}
    combos = []
    for plan in joint_initial_plans(old["outer_proxy"], old["inner_proxy"]):
        screen = o.screen(plan)
        combos.append(
            {
                "plan": plan.to_mapping(),
                "screen": screen.to_mapping(),
                "area_m2": screen.records[0].intervention["cleared_area_m2"],
            }
        )
    valid = [r for r in combos if r["screen"]["protection_goal_met"]]
    selected["independent_sweep"] = min(valid, key=lambda r: r["area_m2"]) if valid else None
    dump(output / "independent-allocation-sweep.json", combos)
    dump(
        output / "selection.json",
        {
            "schema": "joint-frozen-selection-1",
            "protocol_sha256": sha(output / "protocol.json"),
            "selected": selected,
            "held_out_results_read": False,
        },
    )
    print("train-only selections frozen", flush=True)


def confirmation(task):
    output, label, mapping, role, full = task
    o = Oracle(output, full=full)
    plan = plan_from_mapping(mapping)
    screen = o.screen(plan, role, complete=True)
    row = {
        "label": label,
        "role": role,
        "full_grid": full,
        "plan": mapping,
        "screen": screen.to_mapping(),
        "records": [r.to_mapping() for r in screen.records],
    }
    dump(Path(output) / f"confirm-{label}-{role}-{'full' if full else 'crop'}.json", row)
    return row


def finalize(output, workers):
    selection = json.loads((output / "selection.json").read_text())
    o = Oracle(output)
    if selection["protocol_sha256"] != sha(output / "protocol.json"):
        raise ValueError("selection protocol mismatch")
    plans = {"baseline": combined_seed(()).to_mapping()}
    for mode, row in selection["selected"].items():
        if row:
            plans[mode] = row["plan"]
    old = {k: clearing_plan_from_mapping(v) for k, v in o.protocol["historical_plans"].items()}
    plans["historical_combined"] = combined_seed(old.values()).to_mapping()
    tasks = [
        (str(output), label, plan, role, full)
        for label, plan in plans.items()
        for role in ("training", "held_out")
        for full in (False, True)
    ]
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(confirmation, tasks))
    dump(
        output / "report.json",
        {
            "schema": "batuan-joint-pilot-report-1",
            "protocol": o.protocol,
            "selection": selection,
            "confirmation": rows,
            "scope": o.protocol["scope"],
            "material_policy": o.protocol["material_policy"],
        },
    )
    print(json.dumps({"complete": str(output), "confirmation_blocks": len(rows)}), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["prepare", "search", "finalize"], required=True)
    parser.add_argument("--budget", type=int, default=16)
    parser.add_argument("--workers", type=int, default=3)
    a = parser.parse_args()
    out = a.output.resolve()
    if a.phase == "prepare":
        prepare(out, a.budget)
    elif a.phase == "search":
        search(out, a.workers)
    else:
        finalize(out, a.workers)
