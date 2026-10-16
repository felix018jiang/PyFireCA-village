"""Matched-budget 90-minute joint house/bridge protection and shape comparison.

Historical held-out ignition becomes declared training data. New locations are
frozen before search, inspected only after selection. Sensitivity never tunes
or reselects the base-model winners. See the saved protocol for exact scope.
"""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import shutil
import subprocess
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
from run_batuan_adaptive_search import TrainingOracle, digest, dump, save_checked_replay
from run_batuan_intervention_study import candidate, engine_for
from shapely.geometry import Polygon, shape
from shapely.ops import unary_union

from pyfireca.planning.adaptive import (
    CorridorSearchConfig,
    SearchRejected,
    canonical_plan,
    paths_from_plan,
    search_corridors,
)
from pyfireca.planning.contracts import Ignition
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.clearing import (
    ClearingPath,
    clearing_footprint,
    clearing_plan_from_mapping,
)
from pyfireca.planning.sensitivity import ignition_rise_sensitivity
from pyfireca.village.workflow_v3 import crop_v3_case_bundle, load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]
FAMILIES = ("straight", "polyline", "multiple")
ZONES = ("outer_proxy", "inner_proxy")
# Existing S1 and former held-out S3; latter is no longer claimed independent.
TRAIN_CELLS = ((160, 151), (161, 150))
# Predeclared new, neighboring south-side stress tests, not broad geographic validation.
TEST_CELLS = ((162, 150), (160, 152))
WINDOW = (120, 201, 111, 192)


def context(bundle):
    path = Path(bundle.metadata["spatial_assets_dir"]) / "buildings.json"
    structures = json.loads(path.read_text())["buildings"]
    labels = {s["building_id"]: s["properties"]["source_label"] for s in structures}
    types = {int(k): labels[v] for k, v in bundle.metadata["building_labels"].items()}
    near = unary_union(
        [Polygon(p["exterior"], p["holes"]) for s in structures for p in s["polygons"]]
    ).buffer(20)
    return near, types, hashlib.sha256(path.read_bytes()).hexdigest()


def scenarios_from(protocol, key):
    return tuple(
        EvaluationScenario(
            s["scenario_id"],
            tuple(Ignition(tuple(i["point_m"]), i["time_s"]) for i in s["ignitions"]),
            s["horizon_s"],
        )
        for s in protocol[key]
    )


class LockedOracle(TrainingOracle):
    """Cross-process memoization; physical model calls for a plan are serialized."""

    def __call__(self, plan):
        lock = self.directory / (
            digest({"context": self.context, "plan": plan.to_mapping()}) + ".lock"
        )
        with lock.open("a") as stream:
            fcntl.flock(stream, fcntl.LOCK_EX)
            records = super().__call__(plan)
        # Require every path in a multi-path plan to add actual eligible ground.
        # Two duplicates or a dummy second path entirely over roofs cannot win.
        if len(plan.paths) > 1:
            effective = shape(records[0].intervention["effective_footprint"])
            pieces = [clearing_footprint(replace(plan, paths=(p,))) for p in plan.paths]
            for i, piece in enumerate(pieces):
                others = unary_union([p for j, p in enumerate(pieces) if j != i])
                if effective.intersection(piece.difference(others)).area < 1:
                    raise SearchRejected(
                        "each path must contribute >=1 m2 of unique eligible clearance"
                    )
        return records


def family_seeds(zone, common, family):
    """Identical historical locations; prescribed family transformations use no outcomes."""
    for plan in common:
        for path in paths_from_plan(plan):
            a, b = np.array(path.points_m[0]), np.array(path.points_m[-1])
            direction = (b - a) / np.linalg.norm(b - a)
            normal = np.array([-direction[1], direction[0]])
            if family == "straight":
                yield canonical_plan(zone, (ClearingPath((tuple(a), tuple(b)), path.width_m),))
            else:
                for sign in (-1, 1):
                    if family == "polyline":
                        points = (tuple(a), tuple((a + b) / 2 + sign * 10 * normal), tuple(b))
                        paths = (ClearingPath(points, path.width_m),)
                    else:
                        # Preserve the seed's primary route plus a real auxiliary band.
                        middle = (a + b) / 2 + sign * 20 * normal
                        paths = (
                            ClearingPath((tuple(a), tuple(b)), path.width_m),
                            ClearingPath(
                                (tuple(middle - 15 * direction), tuple(middle + 15 * direction)), 5
                            ),
                        )
                    yield canonical_plan(zone, paths)


def search_task(output, zone, family):
    output = Path(output)
    protocol = json.loads((output / "protocol.json").read_text())
    bundle = load_v3_case_bundle(output / "search-case")
    near, types, _ = context(bundle)
    engine = engine_for(bundle, near, types, protocol["code"])
    training = scenarios_from(protocol, "training")
    protected = training + scenarios_from(protocol, "held_out")
    oracle = LockedOracle(
        engine,
        training,
        protected,
        output / "evaluation-cache",
        digest({"protocol": protocol, "stage": "training"}),
    )
    grid = bundle.request.grid
    bounds = (
        grid.x_min_m,
        grid.y_max_m - grid.height * grid.cell_size_m,
        grid.x_min_m + grid.width * grid.cell_size_m,
        grid.y_max_m,
    )
    eligible = (
        bundle.request.burnable_mask & (bundle.request.building_index < 0) & engine.zones[zone]
    )
    anchors = tuple(grid.center(int(r), int(c)) for r, c in np.argwhere(eligible))
    common = [clearing_plan_from_mapping(p) for p in protocol["common_seeds"][zone]]
    config = CorridorSearchConfig(**protocol["configs"][family])
    logpath = output / f"{zone}-{family}-history.jsonl"

    def progress(event):
        with logpath.open("a") as log:
            log.write(json.dumps(event) + "\n")
        if event["event"] == "generation":
            print(json.dumps({"zone": zone, "family": family, **event}), flush=True)

    result = search_corridors(
        zone_id=zone,
        bounds=bounds,
        anchors=anchors,
        seeds=family_seeds(zone, common, family),
        evaluate=oracle,
        config=config,
        progress=progress,
    )
    result["solver_calls_this_invocation"] = oracle.solver_calls
    result["cache_hits_this_invocation"] = oracle.cache_hits
    dump(output / f"{zone}-{family}-search.json", result)
    print(json.dumps({"finished": f"{zone}/{family}", "best": result["best_key"]}), flush=True)
    return zone, family


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--max-evaluations", type=int, default=96)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--seed", type=int, default=20261017)
    parser.add_argument("--rise-factor", type=float, default=0.95)
    parser.add_argument("--resume", action="store_true")
    args = parser.parse_args()
    if args.workers < 1:
        raise ValueError("workers must be positive")
    output = args.output.resolve()
    base_path = ROOT / "runs/batuan-slope-contact-20261016/case"
    base = load_v3_case_bundle(base_path)
    base_sha = request_fingerprint(base.request)
    near, types, structure_sha = context(base)
    # Validate declared sensitivity before any expensive work.
    ignition_rise_sensitivity(base, args.rise_factor)
    old = json.loads((ROOT / "runs/research/batuan-adaptive-20261016-r2/report.json").read_text())
    prior = json.loads(
        (ROOT / "runs/research/batuan-intervention-20261016-r1/report.json").read_text()
    )
    common = {}
    for zone in ZONES:
        old_selected = old["chosen_plans"]["adaptive_" + zone]
        legacy_id = prior["selected"][zone]["representative"]
        legacy = next(p for p in prior["protocol"]["plans"] if p["plan_id"] == legacy_id)
        common[zone] = [old_selected, legacy] + [
            candidate(base.request.grid, zone, row, width, angle).to_mapping()
            for row in ((156, 158) if zone == "outer_proxy" else (150, 153))
            for width in (5, 10)
            for angle in (-30, 0, 30)
        ]

    def make_scenarios(cells, prefix):
        return [
            asdict(
                EvaluationScenario(
                    f"{prefix}-{r}-{c}", (Ignition(base.request.grid.center(r, c)),), 5400
                )
            )
            for r, c in cells
        ]

    source_paths = [
        Path(__file__),
        ROOT / "scripts/run_batuan_adaptive_search.py",
        ROOT / "scripts/run_batuan_intervention_study.py",
    ]
    source_paths += sorted((ROOT / "src/pyfireca/planning").rglob("*.py"))
    source_paths += sorted((ROOT / "src/pyfireca/village").glob("*.py"))
    hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths
    }
    configs = {
        family: asdict(
            CorridorSearchConfig(
                seed=args.seed,
                max_evaluations=args.max_evaluations,
                max_paths=3 if family == "multiple" else 1,
                max_vertices=2 if family == "straight" else 5,
                objective="joint_protection_then_area",
                geometry_family=family,
            )
        )
        for family in FAMILIES
    }
    protocol = {
        "schema": "batuan-joint-protection-study-1",
        "code": digest(hashes),
        "source_hashes": hashes,
        "case_sha256": base_sha,
        "annotated_structures_sha256": structure_sha,
        "zone_geometry_sha256": hashlib.sha256(near.wkb).hexdigest(),
        "window": WINDOW,
        "training": make_scenarios(TRAIN_CELLS, "train90"),
        "held_out": make_scenarios(TEST_CELLS, "held90"),
        "configs": configs,
        "common_seeds": common,
        "prior_selected_plans": {
            k: v for k, v in old["chosen_plans"].items() if k.startswith("adaptive_")
        },
        "sensitivity": {
            "rise_factor": args.rise_factor,
            "selection_feedback": False,
            "status": "predeclared hypothesis, not empirical calibration",
            "formula": "T_ambient + factor * (T_ign_base - T_ambient)",
        },
        "objective": (
            "all training houses AND bridges unignited through 5400s; minimum "
            "actual clearance among ties"
        ),
        "infeasible_fallback": (
            "lex worst total ignited, mean total, worst bridge, worst house, "
            "mean area, earliest ignition"
        ),
        "comparison": (
            "96 requests per class by default; strict geometry classes; "
            "shared physical cache; one optimizer seed"
        ),
        "validation_policy": (
            "freeze all family winners before new held-out or sensitivity "
            "evaluation; no reselection"
        ),
        "previous_holdout_now_training": True,
        "scope": (
            "south-side local forest ignition stress tests; calm, no "
            "spotting; 20m structure-buffer proxy zones"
        ),
        "global_optimality_guaranteed": False,
        "money_objective": False,
    }
    if args.resume:
        saved = json.loads((output / "protocol.json").read_text())
        if digest(saved) != digest(protocol):
            raise ValueError("source, input or protocol mismatch; new output required")
        protocol = saved
    else:
        output.mkdir(parents=True, exist_ok=False)
        dump(output / "protocol.json", protocol)
        dump(
            output / "version.json",
            {
                "parent_git_commit": subprocess.check_output(
                    ["git", "rev-parse", "HEAD"], cwd=ROOT, text=True
                ).strip()
            },
        )
        for p in source_paths:
            target = output / "source_snapshot" / p.relative_to(ROOT)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, target)
        r0, r1, c0, c1 = WINDOW
        crop_v3_case_bundle(
            base_path,
            output / "search-case",
            row_start=r0,
            row_stop=r1,
            col_start=c0,
            col_stop=c1,
            end_time_s=5400,
            sample_interval_s=base.request.sample_interval_s,
        )
    bundle = load_v3_case_bundle(output / "search-case")
    input_lock = {
        "base_case": base_sha,
        "crop_case": request_fingerprint(bundle.request),
        "code": protocol["code"],
    }
    lock_path = output / "input-lock.json"
    if lock_path.exists() and json.loads(lock_path.read_text()) != input_lock:
        raise ValueError("search case mutated")
    dump(lock_path, input_lock)
    engine = engine_for(bundle, near, types, protocol["code"])
    training, held = scenarios_from(protocol, "training"), scenarios_from(protocol, "held_out")
    train_oracle = LockedOracle(
        engine,
        training,
        training + held,
        output / "evaluation-cache",
        digest({"protocol": protocol, "stage": "training"}),
    )
    baseline = canonical_plan("outer_proxy", ())
    train_oracle(baseline)  # A baseline reaching the crop edge aborts before optimization.
    pending = [
        (z, f) for z in ZONES for f in FAMILIES if not (output / f"{z}-{f}-search.json").exists()
    ]
    report_path = output / "report.json"
    if not report_path.exists():
        dump(report_path, {"status": "searching", "protocol": protocol, "finished_searches": []})
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = [pool.submit(search_task, str(output), z, f) for z, f in pending]
        for future in as_completed(futures):
            zone, family = future.result()
            progress = json.loads(report_path.read_text())
            progress["finished_searches"].append(f"{zone}/{family}")
            dump(report_path, progress)
    searches = {
        f"{z}/{f}": json.loads((output / f"{z}-{f}-search.json").read_text())
        for z in ZONES
        for f in FAMILIES
    }
    chosen = {"baseline": baseline}
    best_by_zone = {}
    for z in ZONES:
        for f in FAMILIES:
            search = searches[f"{z}/{f}"]
            record = next(r for r in search["records"] if r["plan_id"] == search["representative"])
            chosen[z + "_" + f] = clearing_plan_from_mapping(record["plan"])
        best_by_zone[z] = min(FAMILIES, key=lambda f: (tuple(searches[f"{z}/{f}"]["best_key"]), f))
    for k, v in protocol["prior_selected_plans"].items():
        chosen["previous_" + k] = clearing_plan_from_mapping(v)
    selected = {k: v.to_mapping() for k, v in chosen.items()}
    selection_sha = digest(selected)
    selection_path = output / "selection.json"
    if (
        selection_path.exists()
        and json.loads(selection_path.read_text())["sha256"] != selection_sha
    ):
        raise AssertionError("resumed selection changed")
    dump(selection_path, {"sha256": selection_sha, "plans": selected, "best_by_zone": best_by_zone})
    report = {
        "status": "validating",
        "protocol": protocol,
        "searches": searches,
        "chosen_plans": selected,
        "best_by_zone": best_by_zone,
        "selection_sha256": selection_sha,
        "controls": {},
        "validation": {},
        "sensitivity": {},
        "full_domain_comparison": {},
    }
    dump(report_path, report)
    test_oracle = LockedOracle(
        engine,
        held,
        training + held,
        output / "validation-cache",
        digest({"protocol": protocol, "stage": "held_out"}),
    )
    sensitive_bundle = ignition_rise_sensitivity(bundle, args.rise_factor)
    sensitive_engine = engine_for(sensitive_bundle, near, types, protocol["code"])
    sensitive_oracle = LockedOracle(
        sensitive_engine,
        training + held,
        training + held,
        output / "sensitivity-cache",
        digest({"protocol": protocol, "stage": "sensitivity"}),
    )
    for label, plan in chosen.items():
        report["controls"][label] = [r.to_mapping() for r in train_oracle(plan)]
        report["validation"][label] = [r.to_mapping() for r in test_oracle(plan)]
        report["sensitivity"][label] = [r.to_mapping() for r in sensitive_oracle(plan)]
        dump(report_path, report)
        print(
            json.dumps(
                {
                    "validated": label,
                    "held": [
                        (r["metrics"]["ignited_house_count"], r["metrics"]["ignited_bridge_count"])
                        for r in report["validation"][label]
                    ],
                }
            ),
            flush=True,
        )
    # Independently check the whole grid, including every protected annotated structure.
    full_engine = engine_for(base, near, types, protocol["code"])
    full_oracle = LockedOracle(
        full_engine,
        training + held,
        training + held,
        output / "full-cache",
        digest({"protocol": protocol, "stage": "full"}),
    )
    for label in ("baseline", *(z + "_" + best_by_zone[z] for z in ZONES)):
        plan = chosen[label]
        records = full_oracle(plan)
        local = report["controls"][label] + report["validation"][label]
        for full, small in zip(records, local, strict=True):
            for key in ("ignited_house_count", "ignited_bridge_count", "ignited_cells"):
                if full.metrics[key] != small["metrics"][key]:
                    raise AssertionError(f"full/crop mismatch {label} {full.scenario_id} {key}")
        viewer = save_checked_replay(
            full_engine, training[0], plan, output / "full" / plan.plan_id, records[0]
        )
        report["full_domain_comparison"][label] = {
            "evaluation": records[0].to_mapping(),
            "all_scenarios": [r.to_mapping() for r in records],
            "viewer": viewer,
        }
        dump(report_path, report)
    if digest(selected) != selection_sha or digest(report["chosen_plans"]) != selection_sha:
        raise AssertionError("post-selection testing changed selection")
    report.update(
        status="completed",
        parent_unchanged=request_fingerprint(base.request) == base_sha,
        held_out_used_for_reselection=False,
        sensitivity_used_for_reselection=False,
    )
    if not report["parent_unchanged"]:
        raise AssertionError("base model was mutated")
    dump(report_path, report)
    print("Completed " + str(output), flush=True)


if __name__ == "__main__":
    main()
