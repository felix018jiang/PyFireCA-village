"""Versioned observed-object joint study with allocation-diverse search.

Primary optimization uses external ignitions. Internal source-building probes
are declared separately and never pooled into external zero-ignition counts.
"""

from __future__ import annotations

import argparse
import json
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from run_batuan_building_pairs import BASE, ROOT, dump, sha
from run_batuan_intervention_study import zones_for
from run_batuan_joint_search import Oracle, digest
from shapely.geometry import Polygon, box
from shapely.ops import unary_union

from pyfireca.planning.adaptive import CorridorSearchConfig, canonical_plan, search_corridors
from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.environments import ProtectionEnvironment, apply_environment
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.joint import (
    JointVillageEvaluationEngine,
    combined_seed,
    joint_initial_plans,
    optimize_joint_corridors,
    plan_from_mapping,
)
from pyfireca.planning.joint_diverse import optimize_diverse_joint
from pyfireca.planning.observations import build_object_observations
from pyfireca.planning.protection import assess_protection, cohort_from_scenario
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import crop_v3_case_bundle, load_v3_case_bundle

WINDOW = (90, 230, 80, 220)
MODES = ("outer_proxy", "inner_proxy", "joint_conditional", "joint_diverse")


def prepare(output, budget):
    output.mkdir(parents=True, exist_ok=False)
    b = load_v3_case_bundle(BASE)
    annotation = Path(b.metadata["spatial_assets_dir"]) / "buildings.json"
    structures = json.loads(annotation.read_text())["buildings"]
    observer = build_object_observations(b.request, structures, b.metadata["building_labels"])
    accepted = json.loads(
        (ROOT / "runs/research/batuan-joint-20261017-r2/selection.json").read_text()
    )
    plans = {r: accepted["selected"][r]["plan"] for r in ("outer_proxy", "inner_proxy")}
    crop_v3_case_bundle(
        BASE,
        output / "search-case",
        row_start=WINDOW[0],
        row_stop=WINDOW[1],
        col_start=WINDOW[2],
        col_stop=WINDOW[3],
        end_time_s=5400,
        sample_interval_s=60,
    )
    envs = {
        name: asdict(ProtectionEnvironment(name, 1.0, speed, direction, 0.2))
        for name, speed, direction in [
            ("sw4", 4, 225),
            ("se2", 2, 135),
            ("west5", 5, 270),
            ("east3", 3, 90),
        ]
    }

    def suite(cells, environments, role):
        rows = []
        for eid in environments:
            for r, c in cells:
                if not b.request.burnable_mask[r, c] or b.request.building_index[r, c] >= 0:
                    raise ValueError("external source is not combustible ground")
                rows.append(
                    {
                        "scenario_id": f"{role}-r{r}c{c}-{eid}",
                        "environment_id": eid,
                        "parent_cell": [r, c],
                        "point_m": b.request.grid.center(r, c),
                        "source_family": "external",
                    }
                )
        return rows

    training = suite(
        [(163, 151), (160, 151), (168, 143), (170, 159), (158, 142), (166, 158)],
        ["sw4", "se2"],
        "design-v2",
    )
    held = suite([(174, 145), (171, 161)], ["west5", "east3"], "held-v2")
    internal = []
    for owner in (162, 328):
        cells = [
            (int(r), int(c))
            for r, c in np.argwhere(b.request.building_index == owner)
            if observer.objects_at_cell(b.request.grid.cell_id(int(r), int(c))) == (owner,)
        ]
        if not cells:
            raise ValueError("no unambiguous internal source cell")
        r, c = max(cells, key=lambda p: float(b.request.parameters.covered_area_m2[p]))
        internal.append(
            {
                "scenario_id": f"internal-v2-B{owner + 1:04d}-sw4",
                "environment_id": "sw4",
                "parent_cell": [r, c],
                "point_m": b.request.grid.center(r, c),
                "source_family": "internal",
                "source_owner": owner,
            }
        )
    protocol = {
        "schema": "batuan-observed-joint-1",
        "horizon_s": 5400,
        "training": training,
        "held_out": held,
        "internal_probes": internal,
        "environments": envs,
        "historical_plans": plans,
        "candidate_budget_per_mode": budget,
        "seed": 20261017,
        "window": WINDOW,
        "base_fingerprint": request_fingerprint(b.request),
        "annotation_sha256": sha(annotation),
        "full_observation": observer.summary(),
        "objective": (
            "zero observed object ignitions for all 90min external design scenarios; "
            "then minimum union ground clearance"
        ),
        "policy": "fixed moisture/synthetic timber; no firebrands, demolition or money",
        "internal_policy": (
            "two single-cell full-domain probes; exclude declared t=0 source; "
            "not in primary optimization"
        ),
        "selection_rule": "freeze design-only selections before reading new held outcomes",
        "source_hashes": {
            str(p.relative_to(ROOT)): sha(p)
            for p in [
                *ROOT.joinpath("src/pyfireca/planning").rglob("*.py"),
                *ROOT.joinpath("src/pyfireca/village").glob("*.py"),
                Path(__file__),
                ROOT / "scripts/run_batuan_joint_search.py",
                ROOT / "scripts/run_batuan_building_pairs.py",
                ROOT / "scripts/run_batuan_intervention_study.py",
                ROOT / "scripts/run_batuan_protection_search.py",
            ]
        },
        "input_hashes": {
            str(p): sha(p)
            for folder in [BASE, output / "search-case"]
            for p in folder.glob("*")
            if p.is_file()
        },
    }
    dump(output / "protocol.json", protocol)
    shutil.copy2(annotation, output / "buildings.source.json")
    for name in protocol["source_hashes"]:
        target = output / "source_snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    print(
        json.dumps(
            {
                "prepared": str(output),
                "design": len(training),
                "held": len(held),
                "internal_probes": len(internal),
                "observed_objects": observer.summary()["object_count"],
            }
        ),
        flush=True,
    )


class ObservedOracle(Oracle):
    def __init__(self, output, full=False):
        self.output = Path(output)
        self.protocol = json.loads((self.output / "protocol.json").read_text())
        for name, h in {**self.protocol["source_hashes"], **self.protocol["input_hashes"]}.items():
            if sha(ROOT / name) != h:
                raise ValueError(f"frozen source/input changed: {name}")
        if sha(self.output / "buildings.source.json") != self.protocol["annotation_sha256"]:
            raise ValueError("frozen annotation changed")
        self.bundle = load_v3_case_bundle(BASE if full else self.output / "search-case")
        structures = json.loads((self.output / "buildings.source.json").read_text())["buildings"]
        self.near = unary_union(
            [Polygon(p["exterior"], p["holes"]) for s in structures for p in s["polygons"]]
        ).buffer(20)
        g = self.bundle.request.grid
        bounds = box(
            g.x_min_m,
            g.y_max_m - g.height * g.cell_size_m,
            g.x_min_m + g.width * g.cell_size_m,
            g.y_max_m,
        )
        included = [
            s
            for s in structures
            if bounds.covers(
                unary_union([Polygon(p["exterior"], p["holes"]) for p in s["polygons"]])
            )
        ]
        names = {s["building_id"] for s in included}
        labels = {k: v for k, v in self.bundle.metadata["building_labels"].items() if v in names}
        self.observer = build_object_observations(self.bundle.request, included, labels)
        self.engines = {}
        self.calls = 0
        self.hits = 0
        self.full = full
        self.model_identity = digest(self.protocol["source_hashes"])
        self.rows = {
            s["scenario_id"]: s
            for key in ["training", "held_out", "internal_probes"]
            for s in self.protocol[key]
        }
        self.cache = self.output / ("full-cache" if full else "cache")
        self.cache.mkdir(exist_ok=True)

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
                object_observer=self.observer,
                structure_types=self.observer.types,
                code_version=self.model_identity,
            )
        return self.engines[eid]


def search_one(task):
    output, mode = task
    o = ObservedOracle(output)
    p = o.protocol
    e = o.engine("sw4")
    old = {r: plan_from_mapping(v) for r, v in p["historical_plans"].items()}
    config = CorridorSearchConfig(
        seed=p["seed"],
        max_evaluations=p["candidate_budget_per_mode"],
        objective="joint_protection_then_area",
        initial_step_m=10,
        max_paths=3,
        max_vertices=5,
    )

    def progress(event):
        dump(Path(output) / f"{mode}-progress.json", event)
        print(
            json.dumps(
                {"mode": mode, "query": event.get("query_index"), "status": event.get("status")}
            ),
            flush=True,
        )

    if mode == "joint_diverse":
        result = optimize_diverse_joint(
            engine=e,
            seeds=joint_initial_plans(*old.values()),
            evaluate=o,
            config=config,
            scenario_ids=tuple(r["scenario_id"] for r in p["training"]),
            progress=progress,
        )
    elif mode == "joint_conditional":
        result = optimize_joint_corridors(
            engine=e,
            seeds=joint_initial_plans(*old.values()),
            evaluate=o,
            config=config,
            progress=progress,
        )
    else:
        grid = e.bundle.request.grid
        mask = e.zones[mode] & grid_mask(e)
        seeds = [
            canonical_plan(
                mode,
                tuple(
                    replace(path, width_m=max(2.5, path.width_m * scale))
                    for path in old[mode].paths
                ),
            )
            for scale in (1, 0.5, 0.75, 1.25)
        ]
        result = search_corridors(
            zone_id=mode,
            bounds=(
                grid.x_min_m,
                grid.y_max_m - grid.height * grid.cell_size_m,
                grid.x_min_m + grid.width * grid.cell_size_m,
                grid.y_max_m,
            ),
            anchors=tuple(grid.center(int(r), int(c)) for r, c in np.argwhere(mask)),
            seeds=seeds,
            evaluate=o,
            config=config,
            progress=progress,
        )
    valid = [r for r in result["records"] if r["status"] == "ok" and r["effectiveness"][0] == 0]
    selected = min(valid, key=lambda r: (r["cleared_area_m2"], r["plan_id"])) if valid else None
    result.update(
        selected=selected,
        physical_solves=o.calls,
        cache_hits=o.hits,
        search_observation=o.observer.summary(),
    )
    dump(Path(output) / f"{mode}-search.json", result)
    return mode, selected


def grid_mask(engine):
    q = engine.bundle.request
    return q.burnable_mask & (q.building_index < 0)


def search(output, workers):
    with ProcessPoolExecutor(workers) as pool:
        selections = dict(pool.map(search_one, [(str(output), mode) for mode in MODES]))
    dump(
        output / "selection.json",
        {
            "protocol_sha256": sha(output / "protocol.json"),
            "selected": selections,
            "held_out_results_read": False,
        },
    )
    print("design-only selections frozen", flush=True)


def confirm_one(task):
    output, label, plan, sid = task
    o = ObservedOracle(output, full=True)
    record = o.evaluate(sid, plan_from_mapping(plan), complete=True)
    raw = o.rows[sid]
    e = o.engine(raw["environment_id"])
    scenario = EvaluationScenario(sid, (Ignition(tuple(raw["point_m"])),), 5400)
    cohort = cohort_from_scenario(
        e.bundle,
        scenario,
        source_family=raw["source_family"],
        structure_types=e.structure_types,
        object_observer=o.observer,
    )
    assessment = assess_protection(record, cohort)
    row = {
        "label": label,
        "scenario_id": sid,
        "source_family": raw["source_family"],
        "record": record.to_mapping(),
        "assessment": assessment,
    }
    folder = Path(output) / "confirm"
    folder.mkdir(exist_ok=True)
    dump(folder / f"{label}-{sid}.json", row)
    print(
        json.dumps(
            {
                "confirmation": label,
                "scenario": sid,
                "protected_ignited": len(assessment["ignited_protected_ids"]),
            }
        ),
        flush=True,
    )
    return row


def confirm(output, workers):
    p = json.loads((output / "protocol.json").read_text())
    selection = json.loads((output / "selection.json").read_text())
    if sha(output / "protocol.json") != selection["protocol_sha256"]:
        raise ValueError("selection/protocol differs")
    plans = {"baseline": combined_seed(()).to_mapping()}
    for mode, row in selection["selected"].items():
        if row:
            plans[mode] = row["plan"]
    # Keep original accepted single-side plans as fixed reference if new suite
    # makes them infeasible; failures stay visible rather than dropping a side.
    for region, plan in p["historical_plans"].items():
        if region not in plans:
            plans["reference_" + region] = plan
    tasks = [
        (str(output), label, plan, s["scenario_id"])
        for label, plan in plans.items()
        for role in ["training", "held_out", "internal_probes"]
        for s in p[role]
    ]
    with ProcessPoolExecutor(workers) as pool:
        rows = list(pool.map(confirm_one, tasks))
    dump(
        output / "report.json",
        {
            "protocol_sha256": sha(output / "protocol.json"),
            "selection": selection,
            "plans": plans,
            "confirmation": rows,
            "scope": "333 observed labels; nested semantics unresolved; synthetic physics",
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["prepare", "search", "confirm"], required=True)
    parser.add_argument("--budget", type=int, default=32)
    parser.add_argument("--workers", type=int, default=4)
    args = parser.parse_args()
    if args.phase == "prepare":
        prepare(args.output.resolve(), args.budget)
    elif args.phase == "search":
        search(args.output.resolve(), args.workers)
    else:
        confirm(args.output.resolve(), args.workers)
