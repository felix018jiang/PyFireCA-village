"""Finite-scenario 90-minute protection-constrained geometry optimization.

Each accepted plan must pass every training ignition x wind x material profile.
First-failure screening avoids useless remaining solves for infeasible plans.
The latter's search losses are diagnostic lower bounds, never full-suite risks.
Held-out scenarios are evaluated only after both zones' selections are frozen.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from pathlib import Path

import numpy as np
from run_batuan_adaptive_search import digest, dump
from run_batuan_intervention_study import engine_for
from run_batuan_protection_search import context, family_seeds
from shapely.geometry import box

from pyfireca.planning.adaptive import (
    CorridorSearchConfig,
    SearchRejected,
    canonical_plan,
    paths_from_plan,
    search_corridors,
)
from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.environments import ProtectionEnvironment, apply_environment
from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario
from pyfireca.planning.interventions.clearing import (
    ClearingPath,
    clearing_footprint,
    clearing_plan_from_mapping,
)
from pyfireca.planning.robust import screen_protection
from pyfireca.village.workflow_v3 import crop_v3_case_bundle, load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]
PREVIOUS = ROOT / "runs/research/batuan-protection90-20261016-r2"
BASE = ROOT / "runs/batuan-slope-contact-20261016/case"
WINDOW = (90, 230, 80, 220)
TRAIN_CELLS = ((163, 151), (160, 151), (168, 143), (170, 159))
TEST_CELLS = ((171, 146), (171, 156))
ZONES = ("outer_proxy", "inner_proxy")


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def source_files():
    paths = list((ROOT / "src/pyfireca/planning").rglob("*.py"))
    paths += list((ROOT / "src/pyfireca/village").glob("*.py"))
    paths += [
        ROOT / "scripts" / name
        for name in (
            "run_batuan_multiscenario_search.py",
            "run_batuan_protection_search.py",
            "run_batuan_adaptive_search.py",
            "run_batuan_intervention_study.py",
        )
    ]
    return sorted(paths)


def verify_sources(protocol):
    for name, expected in protocol["source_hashes"].items():
        if file_sha(ROOT / name) != expected:
            raise ValueError(f"frozen source changed: {name}")
    for name, expected in protocol["input_hashes"].items():
        if file_sha(name) != expected:
            raise ValueError(f"frozen input changed: {name}")


def scenario(raw):
    return EvaluationScenario(
        raw["scenario_id"],
        tuple(Ignition(tuple(p), 0) for p in raw["points_m"]),
        5400,
    )


def environments():
    result = {}
    for label, speed, direction in (
        ("calm", 0, 0),
        ("southeast2", 2, 135),
        ("southwest4", 4, 225),
        ("south3", 3, 180),
        ("north3", 3, 0),
    ):
        for factor in (1.0, 0.95):
            name = f"{label}-rise{factor:g}"
            result[name] = asdict(ProtectionEnvironment(name, factor, speed, direction, 0.2))
    return result


def prepare(output, budget, replicas):
    if output.exists():
        raise FileExistsError("use a new directory for a new protocol")
    base = load_v3_case_bundle(BASE)
    old = json.loads((PREVIOUS / "report.json").read_text())
    ablation = json.loads((PREVIOUS / "component-ablation.json").read_text())
    seeds, controls = {}, {}
    for zone in ZONES:
        common = [
            clearing_plan_from_mapping(old["chosen_plans"][f"{zone}_{f}"])
            for f in ("straight", "polyline", "multiple")
        ]
        if zone == "outer_proxy":
            common.insert(0, clearing_plan_from_mapping(ablation["components"][0]["plan"]))
        controls[zone] = common[0].to_mapping()
        values = [canonical_plan(zone, paths_from_plan(p)) for p in common]
        # Declared, outcome-independent expansions of previous main routes.
        for plan in common[:1]:
            path = paths_from_plan(plan)[0]
            a, b = np.array(path.points_m[0]), np.array(path.points_m[-1])
            middle = (a + b) / 2
            for length_factor in (1.25, 1.5):
                for width in (10.0, 15.0, 20.0):
                    points = (
                        tuple(middle + (a - middle) * length_factor),
                        tuple(middle + (b - middle) * length_factor),
                    )
                    values.append(canonical_plan(zone, (ClearingPath(points, width),)))
        values += list(family_seeds(zone, common[:1], "polyline"))
        values += list(family_seeds(zone, common[:1], "multiple"))
        seeds[zone] = list({p.plan_id: p.to_mapping() for p in values}.values())
    envs = environments()
    # Order is predeclared, includes both legacy failure locations and broader flanks.
    train_envs = [
        f"{w}-rise{f:g}" for w in ("southwest4", "southeast2", "calm") for f in (0.95, 1.0)
    ]
    test_envs = [f"{w}-rise{f:g}" for w in ("south3", "north3") for f in (0.95, 1.0)]

    def suite(cells, names, role):
        return [
            dict(
                scenario_id=f"{role}-r{r}c{c}-{env}",
                environment_id=env,
                parent_cell=[r, c],
                points_m=[base.request.grid.center(r, c)],
            )
            for env in names
            for r, c in cells
        ]

    for r, c in TRAIN_CELLS + TEST_CELLS:
        if not base.request.burnable_mask[r, c] or base.request.building_index[r, c] >= 0:
            raise ValueError("fire sources must be burnable vegetation")
    sources = {str(p.relative_to(ROOT)): file_sha(p) for p in source_files()}
    input_paths = [
        *BASE.glob("*"),
        PREVIOUS / "report.json",
        PREVIOUS / "component-ablation.json",
    ]
    _, _, _ = context(base)
    input_paths += [Path(base.metadata["spatial_assets_dir"]) / "buildings.json"]
    protocol = {
        "schema": "batuan-multiscenario-protection-1",
        "date": "2026-10-17",
        "code_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
        ).strip(),
        "physical_code": digest(sources),
        "source_hashes": sources,
        "input_hashes": {str(p): file_sha(p) for p in input_paths if p.is_file()},
        "base_case_sha256": request_fingerprint(base.request),
        "window": WINDOW,
        "horizon_s": 5400,
        "environments": envs,
        "training": suite(TRAIN_CELLS, train_envs, "design"),
        "held_out": suite(TEST_CELLS, test_envs, "unseen"),
        "configs": [
            asdict(
                CorridorSearchConfig(
                    seed=20261017 + i,
                    max_evaluations=budget,
                    geometry_family="any",
                    objective="joint_protection_then_area",
                    initial_step_m=10,
                )
            )
            for i in range(replicas)
        ],
        "seeds": seeds,
        "previous_controls": controls,
        "screening": (
            "first failure proves infeasible; pass requires all 24; partial losses are lower bounds"
        ),
        "objective": (
            "all declared design scenarios zero houses AND bridges, then minimum effective area"
        ),
        "selection": (
            "best certified feasible across replicas; if none, report infeasible, never zero loss"
        ),
        "validation": (
            "8 unseen source x wind x rise scenarios after selection; never "
            "reselect using their results"
        ),
        "scope": (
            "140x140 expanded window, 5m cells; proxy20m building buffer; "
            "synthetic static tilt, no embers"
        ),
        "geometry": (
            "<=3 paths, <=5 nodes/path, width2.5-30m length10-240m; removal "
            "and simplification allowed"
        ),
    }
    output.mkdir(parents=True)
    for name in sources:
        dest = output / "source_snapshot" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, dest)
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
    protocol["crop_case_sha256"] = request_fingerprint(cropped.request)
    dump(output / "protocol.json", protocol)
    dump(
        output / "status.json",
        {"status": "prepared", "training_scenarios": 24, "held_out_scenarios": 8},
    )
    return protocol


class ScenarioOracle:
    """Cache every physical solve by full environment/scenario/geometry identity."""

    def __init__(self, output, *, full=False):
        self.output = Path(output)
        self.protocol = json.loads((self.output / "protocol.json").read_text())
        verify_sources(self.protocol)
        self.bundle = load_v3_case_bundle(BASE if full else self.output / "search-case")
        expected = self.protocol["base_case_sha256" if full else "crop_case_sha256"]
        if request_fingerprint(self.bundle.request) != expected:
            raise ValueError("case fingerprint changed")
        self.full = full
        self.near, self.types, _ = context(self.bundle)
        self.engines = {}
        self.rows = {
            s["scenario_id"]: s for s in self.protocol["training"] + self.protocol["held_out"]
        }
        self.cache = self.output / ("full-cache" if full else "evaluation-cache")
        self.cache.mkdir(exist_ok=True)
        self.calls = self.hits = 0
        grid = self.bundle.request.grid
        self.protected = []
        for row in self.rows.values():
            x, y = row["points_m"][0]
            h = grid.cell_size_m / 2
            self.protected.append(box(x - h, y - h, x + h, y + h))

    def engine(self, environment_id):
        if environment_id not in self.engines:
            env = ProtectionEnvironment(**self.protocol["environments"][environment_id])
            bundle = apply_environment(self.bundle, env)
            engine = engine_for(bundle, self.near, self.types, self.protocol["physical_code"])
            engine.capabilities = {
                **engine.capabilities,
                "static_wind_radiative_tilt": True,
                "wind_driven_contact": False,
                "weather_empirically_calibrated": False,
            }
            self.engines[environment_id] = engine
        return self.engines[environment_id]

    def preflight(self, plan):
        footprint = clearing_footprint(plan)
        if any(footprint.intersection(c).area > 1e-8 for c in self.protected):
            raise SearchRejected("layout overlaps a protected design or held-out source cell")

    def evaluate(self, scenario_id, plan, *, complete=False):
        row = self.rows[scenario_id]
        engine = self.engine(row["environment_id"])
        sc = scenario(row)
        signature = hashlib.sha256()
        for item in (
            engine.case_sha256,
            engine.code_version,
            asdict(engine.transport),
            asdict(sc),
            plan.to_mapping(),
            engine.structure_types,
        ):
            signature.update(json.dumps(item, sort_keys=True).encode())
        signature.update(engine.zones[plan.zone_id].tobytes())
        if not complete:
            signature.update(b"stop_at_first_structure_ignition")
        key = signature.hexdigest()
        path = self.cache / (key + ".json")
        with (self.cache / (key + ".lock")).open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            if path.exists():
                raw = json.loads(path.read_text())
                if digest(raw["record"]) != raw["record_sha256"]:
                    raise ValueError("corrupted physical evaluation cache")
                record = EvaluationResult(**raw["record"])
                self.hits += 1
            else:
                record = engine.evaluate(sc, plan, stop_at_first_structure_ignition=not complete)
                self.calls += 1
                dump(
                    path,
                    {"record": record.to_mapping(), "record_sha256": digest(record.to_mapping())},
                )
        if record.evaluation_sha256 != key or record.scenario_id != scenario_id:
            raise ValueError("physical evaluation signature mismatch")
        return record

    def screen(self, plan, *, suite="training", complete=False):
        self.preflight(plan)
        ids = [s["scenario_id"] for s in self.protocol[suite]]
        try:
            screened = screen_protection(
                ids,
                lambda sid: self.evaluate(sid, plan, complete=complete),
                stop_at_failure=not complete,
            )
        except ValueError as exc:
            if "domain-edge" in str(exc):
                raise SearchRejected(str(exc)) from exc
            raise
        directory = self.output / ("full-screens" if self.full else "screens") / suite
        directory.mkdir(parents=True, exist_ok=True)
        dump(directory / (plan.plan_id + ".json"), screened.to_mapping())
        return screened

    def __call__(self, plan):
        return list(self.screen(plan).records)


def search_task(output, zone, replica):
    oracle = ScenarioOracle(output)
    protocol = oracle.protocol
    config = CorridorSearchConfig(**protocol["configs"][replica])
    grid = oracle.bundle.request.grid
    base_engine = oracle.engine("calm-rise1")
    allowed = oracle.bundle.request.burnable_mask & (oracle.bundle.request.building_index < 0)
    allowed &= base_engine.zones[zone]
    anchors = tuple(grid.center(int(r), int(c)) for r, c in np.argwhere(allowed))
    bounds = (
        grid.x_min_m,
        grid.y_max_m - grid.height * grid.cell_size_m,
        grid.x_min_m + grid.width * grid.cell_size_m,
        grid.y_max_m,
    )
    path = Path(output) / f"{zone}-replica{replica}-history.jsonl"
    # Deterministic regeneration on resume, no duplicate append of old histories.
    temp = path.with_suffix(".rebuilding.jsonl")

    def progress(event):
        with temp.open("a") as log:
            log.write(json.dumps(event) + "\n")
        if event["event"] == "evaluation":
            print(
                json.dumps(
                    {
                        "zone": zone,
                        "replica": replica,
                        "query": event["query_index"],
                        "status": event["status"],
                        "protection_lower_bound": event.get("effectiveness", [None])[0],
                        "area_m2": event.get("cleared_area_m2"),
                    }
                ),
                flush=True,
            )

    temp.write_text("")
    result = search_corridors(
        zone_id=zone,
        bounds=bounds,
        anchors=anchors,
        seeds=[clearing_plan_from_mapping(p) for p in protocol["seeds"][zone]],
        evaluate=oracle,
        config=config,
        progress=progress,
    )
    temp.replace(path)
    for record in result["records"]:
        if record["status"] == "ok":
            screen = json.loads(
                (Path(output) / "screens/training" / (record["plan_id"] + ".json")).read_text()
            )
            record["protection_screen"] = screen
    result.update(
        physical_solves_this_invocation=oracle.calls,
        cache_hits_this_invocation=oracle.hits,
        ranking_note="partial infeasible scores are lower-bound diagnostics; zero requires all 24",
    )
    dump(Path(output) / f"{zone}-replica{replica}-search.json", result)
    return zone, replica


def search(output, workers):
    protocol = json.loads((output / "protocol.json").read_text())
    verify_sources(protocol)
    dump(output / "status.json", {"status": "searching", "training_scenarios": 24})
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks = [
            pool.submit(search_task, str(output), z, i)
            for z in ZONES
            for i in range(len(protocol["configs"]))
        ]
        for future in as_completed(tasks):
            print({"finished": future.result()}, flush=True)
    chosen = {}
    for zone in ZONES:
        records = [
            r
            for i in range(len(protocol["configs"]))
            for r in json.loads((output / f"{zone}-replica{i}-search.json").read_text())["records"]
            if r["status"] == "ok"
        ]
        feasible = [r for r in records if r["protection_screen"]["protection_goal_met"]]
        pool = feasible or records
        best = min(
            pool, key=lambda r: (tuple(r["effectiveness"]), r["cleared_area_m2"], r["plan_id"])
        )
        chosen[zone] = {
            "plan": best["plan"],
            "training_goal_met": bool(feasible),
            "cleared_area_m2": best["cleared_area_m2"],
            "protection_screen": best["protection_screen"],
        }
    selection = {
        "schema": "multiscenario-frozen-selection-1",
        "protocol_sha256": digest(protocol),
        "chosen": chosen,
        "held_out_results_read": False,
    }
    frozen = output / "selection.json"
    if frozen.exists() and digest(json.loads(frozen.read_text())) != digest(selection):
        raise ValueError("resume would change frozen selection")
    dump(frozen, selection)
    dump(
        output / "status.json",
        {"status": "selection_frozen", "selection_sha256": digest(selection)},
    )


def final_task(output, label, raw, suite):
    oracle = ScenarioOracle(output)
    plan = clearing_plan_from_mapping(raw)
    screen = oracle.screen(plan, suite=suite, complete=True)
    result = {
        "label": label,
        "suite": suite,
        "screen": screen.to_mapping(),
        "records": [r.to_mapping() for r in screen.records],
    }
    dump(Path(output) / "final-evaluations" / (label + "-" + suite + ".json"), result)
    return label, suite


def finalize(output, workers):
    protocol = json.loads((output / "protocol.json").read_text())
    selection = json.loads((output / "selection.json").read_text())
    if selection["protocol_sha256"] != digest(protocol):
        raise ValueError("frozen selection protocol mismatch")
    plans = {"baseline": canonical_plan("outer_proxy", ()).to_mapping()}
    plans.update({f"previous_{z}": p for z, p in protocol["previous_controls"].items()})
    plans.update({z: v["plan"] for z, v in selection["chosen"].items()})
    with ProcessPoolExecutor(max_workers=workers) as pool:
        tasks = [
            pool.submit(final_task, str(output), label, plan, suite)
            for label, plan in plans.items()
            for suite in ("training", "held_out")
        ]
        for future in as_completed(tasks):
            print({"finalized": future.result()}, flush=True)
    report = {
        "schema": "batuan-multiscenario-report-1",
        "protocol": protocol,
        "selection_sha256": digest(selection),
        "selection": selection,
        "plans": plans,
        "evaluations": {},
    }
    for label in plans:
        report["evaluations"][label] = {
            suite: json.loads(
                (output / "final-evaluations" / (label + "-" + suite + ".json")).read_text()
            )
            for suite in ("training", "held_out")
        }
    report["counts"] = {
        "search_requests": sum(
            len(json.loads(p.read_text())["records"]) for p in output.glob("*-search.json")
        ),
        "physical_cache_records": len(list((output / "evaluation-cache").glob("*.json"))),
    }
    dump(output / "report.json", report)
    dump(output / "status.json", {"status": "completed", "selection_sha256": digest(selection)})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--phase", choices=("prepare", "search", "finalize"), required=True)
    p.add_argument("--budget", type=int, default=64)
    p.add_argument("--replicas", type=int, default=2)
    p.add_argument("--workers", type=int, default=4)
    args = p.parse_args()
    if min(args.budget, args.replicas, args.workers) < 1:
        raise ValueError("budgets, replicas and workers must be positive")
    output = args.output.resolve()
    if args.phase == "prepare":
        prepare(output, args.budget, args.replicas)
    elif args.phase == "search":
        search(output, args.workers)
    else:
        finalize(output, args.workers)


if __name__ == "__main__":
    main()
