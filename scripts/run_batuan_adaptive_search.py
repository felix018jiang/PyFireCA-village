"""Adaptive bent/multiple vegetation corridors, with independently frozen checks.

Default local window reproduces the existing Batuan south-side stress test.
--search-domain full enables the entire scene; neither domain is a surveyed boundary.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import asdict
from pathlib import Path

import numpy as np
from render_batuan_intervention_report import geographic_feature
from run_batuan_intervention_study import SOURCES, candidate, engine_for, save_replay
from shapely.geometry import Polygon, box, mapping
from shapely.ops import unary_union

from pyfireca.planning.adaptive import (
    CorridorSearchConfig,
    SearchRejected,
    canonical_plan,
    paths_from_plan,
    search_corridors,
)
from pyfireca.planning.contracts import Ignition, require_number
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.evaluation import EvaluationResult, EvaluationScenario
from pyfireca.planning.interventions.clearing import (
    InterventionConflict,
    clearing_footprint,
    clearing_plan_from_mapping,
)
from pyfireca.village.workflow_v3 import crop_v3_case_bundle, load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]


def dump(path, value):
    """Atomic checkpoint; never leave a truncated report after interruption."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    os.replace(temporary, path)


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class TrainingOracle:
    """Context-bound on-disk memoization; never reads validation outcomes."""

    def __init__(self, engine, scenarios, protected_scenarios, directory, context):
        self.engine, self.scenarios = engine, scenarios
        self.directory = Path(directory)
        self.context = digest(
            {
                "experiment": context,
                "case": engine.case_sha256,
                "code": engine.code_version,
                "scenarios": [asdict(s) for s in scenarios],
                "protected_scenarios": [asdict(s) for s in protected_scenarios],
                "zone_masks": {
                    k: hashlib.sha256(v.tobytes()).hexdigest() for k, v in engine.zones.items()
                },
                "structure_types": engine.structure_types,
            }
        )
        self.directory.mkdir(parents=True, exist_ok=True)
        self.cache_hits = self.solver_calls = 0
        grid = engine.bundle.request.grid
        self.protected_cells = []
        for scenario in protected_scenarios:
            for ignition in scenario.ignitions:
                row, col = grid.locate(*ignition.point_m)
                x, y = grid.center(row, col)
                self.protected_cells.append(
                    box(
                        x - grid.cell_size_m / 2,
                        y - grid.cell_size_m / 2,
                        x + grid.cell_size_m / 2,
                        y + grid.cell_size_m / 2,
                    )
                )

    def __call__(self, plan):
        key = digest({"context": self.context, "plan": plan.to_mapping()})
        path = self.directory / (key + ".json")
        if path.exists():
            stored = json.loads(path.read_text())
            if stored["key"] != key or stored["payload_sha256"] != digest(stored["payload"]):
                raise ValueError("invalid evaluation cache checksum")
            self.cache_hits += 1
            payload = stored["payload"]
        else:
            try:
                footprint = clearing_footprint(plan)
                if any(footprint.intersection(cell).area > 1e-8 for cell in self.protected_cells):
                    raise SearchRejected("candidate overlaps a protected ignition cell")
                evaluations = []
                for scenario in self.scenarios:
                    result = self.engine.evaluate(scenario, plan)
                    self.solver_calls += 1
                    if result.metrics["burning_reached_domain_edge"]:
                        raise SearchRejected("training fire reached domain edge; invalid window")
                    evaluations.append(result.to_mapping())
                payload = {"status": "ok", "evaluations": evaluations}
            except InterventionConflict as exc:
                payload = {"status": "rejected", "reason": str(exc)}
            dump(path, {"key": key, "payload": payload, "payload_sha256": digest(payload)})
        if payload["status"] == "rejected":
            raise SearchRejected(payload["reason"])
        return [EvaluationResult(**r) for r in payload["evaluations"]]


def save_checked_replay(engine, scenario, plan, root, record):
    """Resume completed replays; preserve interrupted exports in sibling folders."""
    marker = root / "replay-complete.json"
    if marker.exists():
        if json.loads(marker.read_text())["evaluation_sha256"] != record.evaluation_sha256:
            raise ValueError("replay marker differs from evaluation")
        viewer = str(root / "run/viewer")
    else:
        if root.exists():
            suffix = 1
            while root.with_name(root.name + f".interrupted-{suffix}").exists():
                suffix += 1
            root.rename(root.with_name(root.name + f".interrupted-{suffix}"))
        viewer = save_replay(engine, scenario, plan, root)
    times = np.load(root / "run/outputs/ignition_time_s.npy")
    if int(np.isfinite(times).sum()) != record.metrics["ignited_cells"]:
        raise AssertionError("replay/evaluation ignition count mismatch")
    request = engine.bundle.request
    for category, key in (
        ("building", "ignited_house_count"),
        ("fengyuqiao", "ignited_bridge_count"),
    ):
        affected = {
            int(bid)
            for bid in np.unique(request.building_index[np.isfinite(times)])
            if bid >= 0 and engine.structure_types.get(int(bid), "building") == category
        }
        if len(affected) != record.metrics[key]:
            raise AssertionError("replay/evaluation structure count mismatch")
    dump(marker, {"evaluation_sha256": record.evaluation_sha256})
    return viewer


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepared", type=Path, default=ROOT / "runs/batuan-slope-contact-20261016"
    )
    parser.add_argument(
        "--previous", type=Path, default=ROOT / "runs/research/batuan-intervention-20261016-r1"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--max-evaluations",
        type=int,
        default=64,
        help="unique oracle requests per zone, including rejected plans",
    )
    parser.add_argument("--seed", type=int, default=20261016)
    parser.add_argument("--max-paths", type=int, default=3)
    parser.add_argument("--max-vertices", type=int, default=5)
    parser.add_argument("--width-min-m", type=float, default=2.5)
    parser.add_argument("--width-max-m", type=float, default=30)
    parser.add_argument("--horizon-s", type=float, default=3600)
    parser.add_argument("--extended-horizon-s", type=float, default=5400)
    parser.add_argument(
        "--objective",
        choices=("effectiveness_only", "effectiveness_then_area"),
        default="effectiveness_then_area",
    )
    parser.add_argument("--search-domain", choices=("local", "full"), default="local")
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--skip-replays", action="store_true")
    args = parser.parse_args()
    require_number("horizon_s", args.horizon_s, positive=True)
    require_number("extended_horizon_s", args.extended_horizon_s, positive=True)
    config = CorridorSearchConfig(
        seed=args.seed,
        max_evaluations=args.max_evaluations,
        max_paths=args.max_paths,
        max_vertices=args.max_vertices,
        width_min_m=args.width_min_m,
        width_max_m=args.width_max_m,
        objective=args.objective,
    )
    if args.horizon_s <= 0 or args.extended_horizon_s <= args.horizon_s:
        raise ValueError("validation horizon must exceed the positive training horizon")
    output = args.output.resolve()
    if args.resume:
        if not (output / "protocol.json").exists():
            raise FileNotFoundError("resume requires a saved protocol")
    else:
        output.mkdir(parents=True, exist_ok=False)
    parent = load_v3_case_bundle(args.prepared / "case")
    before = request_fingerprint(parent.request)
    previous = json.loads((args.previous / "report.json").read_text())
    if previous["protocol"]["case_sha256"] != before:
        raise ValueError("legacy seed study and current base case do not match")
    paths = [
        Path(__file__),
        ROOT / "src/pyfireca/planning/adaptive.py",
        ROOT / "src/pyfireca/planning/interventions/clearing.py",
        ROOT / "src/pyfireca/planning/engines/village.py",
        ROOT / "src/pyfireca/planning/effectiveness.py",
        ROOT / "src/pyfireca/planning/evaluation.py",
        ROOT / "scripts/run_batuan_intervention_study.py",
        ROOT / "scripts/render_batuan_intervention_report.py",
        ROOT / "scripts/render_batuan_adaptive_report.py",
    ]
    paths.extend(sorted((ROOT / "src/pyfireca/village").glob("*.py")))
    hashes = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    code = digest(hashes)
    structures = json.loads(
        (Path(parent.metadata["spatial_assets_dir"]) / "buildings.json").read_text()
    )["buildings"]
    labels = {s["building_id"]: s["properties"]["source_label"] for s in structures}
    types = {int(k): labels[v] for k, v in parent.metadata["building_labels"].items()}
    near = unary_union(
        [Polygon(p["exterior"], p["holes"]) for s in structures for p in s["polygons"]]
    ).buffer(20)
    scenarios = [
        EvaluationScenario(
            f"forest-{r}-{c}", (Ignition(parent.request.grid.center(r, c)),), args.horizon_s
        )
        for r, c in SOURCES
    ]
    window = (
        (120, 201, 111, 192)
        if args.search_domain == "local"
        else (0, parent.request.grid.height, 0, parent.request.grid.width)
    )
    protocol = {
        "schema": "batuan-adaptive-study-1",
        "parent_git_commit": head,
        "code": code,
        "source_hashes": hashes,
        "case_sha256": before,
        "annotated_structures_sha256": hashlib.sha256(
            (Path(parent.metadata["spatial_assets_dir"]) / "buildings.json").read_bytes()
        ).hexdigest(),
        "zone_geometry_sha256": hashlib.sha256(near.wkb).hexdigest(),
        "previous_protocol_sha256": digest(previous["protocol"]),
        "config": asdict(config),
        "window": window,
        "training": [asdict(s) for s in scenarios[:2]],
        "held_out": asdict(scenarios[2]),
        "extended_horizon_s": args.extended_horizon_s,
        "search_domain": args.search_domain,
        "skip_replays": args.skip_replays,
        "zone_assumption": "20m building buffer proxies, not surveyed village boundary",
        "scope": "south-side forest ignition stress tests; calm, no spotting",
        "area_policy": "union of actual cleared vegetation, not requested corridor area",
        "money_objective": False,
        "global_optimality_guaranteed": False,
    }
    # SHA of code rather than moving HEAD is the resume compatibility key.
    if args.resume:
        saved = json.loads((output / "protocol.json").read_text())
        left, right = dict(saved), dict(protocol)
        left.pop("parent_git_commit")
        right.pop("parent_git_commit")
        if digest(left) != digest(right):
            raise ValueError("resume protocol/input/code mismatch; use a new output directory")
        protocol = saved
    else:
        dump(output / "protocol.json", protocol)
        for p in paths:
            destination = output / "source_snapshot" / p.relative_to(ROOT)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(p, destination)
        r0, r1, c0, c1 = window
        crop_v3_case_bundle(
            args.prepared / "case",
            output / "search-case",
            row_start=r0,
            row_stop=r1,
            col_start=c0,
            col_stop=c1,
            end_time_s=args.horizon_s,
            sample_interval_s=parent.request.sample_interval_s,
        )
    bundle = load_v3_case_bundle(output / "search-case")
    engine = engine_for(bundle, near, types, code)
    input_lock = output / "input-lock.json"
    expected_inputs = {
        "base_case_sha256": before,
        "crop_case_sha256": engine.case_sha256,
        "code": code,
    }
    if input_lock.exists():
        if json.loads(input_lock.read_text()) != expected_inputs:
            raise ValueError("saved search case differs from frozen inputs")
    else:
        dump(input_lock, expected_inputs)
    grid = bundle.request.grid
    bounds = (
        grid.x_min_m,
        grid.y_max_m - grid.height * grid.cell_size_m,
        grid.x_min_m + grid.width * grid.cell_size_m,
        grid.y_max_m,
    )
    dump(
        output / "search-zones.geojson",
        geographic_feature(mapping(near), {"status": "proxy"}, grid.crs),
    )
    oracle = TrainingOracle(
        engine,
        scenarios[:2],
        scenarios,
        output / "evaluation-cache",
        digest({"protocol": protocol, "crop_case": engine.case_sha256, "structure_types": types}),
    )
    # Require a valid no-treatment boundary check before searching protective interventions.
    baseline = canonical_plan("outer_proxy", ())
    oracle(baseline)
    report = {
        "status": "searching",
        "protocol": protocol,
        "searches": {},
        "controls": {},
        "validation": {},
        "full_domain_comparison": {},
    }
    dump(output / "report.json", report)
    chosen = {"baseline": baseline}
    for zone in ("outer_proxy", "inner_proxy"):
        selected = previous["selected"][zone]["representative"]
        raw = next(p for p in previous["protocol"]["plans"] if p["plan_id"] == selected)
        legacy = canonical_plan(zone, paths_from_plan(clearing_plan_from_mapping(raw)))
        chosen["legacy_" + zone] = legacy
        eligible = (
            bundle.request.burnable_mask & (bundle.request.building_index < 0) & engine.zones[zone]
        )
        anchors = tuple(grid.center(int(r), int(c)) for r, c in np.argwhere(eligible))
        seeds = [
            legacy,
            *[
                candidate(parent.request.grid, zone, row, width, angle)
                for row in ((156, 158) if zone == "outer_proxy" else (150, 153))
                for width in (5, 10)
                for angle in (-30, 0, 30)
            ],
        ]

        def progress(event, zone=zone):
            with (output / "history.jsonl").open("a") as log:
                log.write(json.dumps({"zone": zone, **event}, ensure_ascii=False) + "\n")
            if event["event"] == "evaluation":
                print(
                    json.dumps(
                        {
                            "zone": zone,
                            "query": event["query_index"],
                            "operator": event["operator"],
                            "status": event["status"],
                            "effectiveness": event.get("effectiveness"),
                            "area_m2": event.get("cleared_area_m2"),
                            "cache_hits": oracle.cache_hits,
                            "solver_calls": oracle.solver_calls,
                        }
                    ),
                    flush=True,
                )

        result = search_corridors(
            zone_id=zone,
            bounds=bounds,
            anchors=anchors,
            seeds=seeds,
            evaluate=oracle,
            config=config,
            progress=progress,
        )
        report["searches"][zone] = result
        dump(output / (zone + "-search.json"), result)
        raw = next(r["plan"] for r in result["records"] if r["plan_id"] == result["representative"])
        chosen["adaptive_" + zone] = clearing_plan_from_mapping(raw)
        dump(output / "report.json", report)
    # Selection is frozen here; validation results are never passed to the searcher.
    report["status"] = "validation_pending"
    report["chosen_plans"] = {label: p.to_mapping() for label, p in chosen.items()}
    report["selection_sha256"] = digest(report["chosen_plans"])
    dump(output / "report.json", report)
    extended = EvaluationScenario(
        scenarios[2].scenario_id + "-extended", scenarios[2].ignitions, args.extended_horizon_s
    )
    validation_oracle = TrainingOracle(
        engine,
        (scenarios[2], extended),
        scenarios,
        output / "validation-cache",
        digest({"protocol": protocol, "stage": "held_out"}),
    )
    for label, plan in chosen.items():
        records = oracle(plan)
        report["controls"][label] = [r.to_mapping() for r in records]
        normal, longer = validation_oracle(plan)
        report["validation"][label] = {
            "held_out": normal.to_mapping(),
            "extended": longer.to_mapping(),
        }
        dump(output / "report.json", report)
        print(
            json.dumps(
                {
                    "validation": label,
                    "held_out_houses": normal.metrics["ignited_house_count"],
                    "extended_houses": longer.metrics["ignited_house_count"],
                }
            ),
            flush=True,
        )
    full = engine_for(parent, near, types, code)
    full_oracle = TrainingOracle(
        full,
        (scenarios[0],),
        scenarios,
        output / "full-evaluation-cache",
        digest({"protocol": protocol, "stage": "full"}),
    )
    for label in ("baseline", "adaptive_outer_proxy", "adaptive_inner_proxy"):
        plan = chosen[label]
        record = full_oracle(plan)[0]
        local = report["controls"][label][0]
        for key in ("ignited_house_count", "ignited_bridge_count", "ignited_cells"):
            if record.metrics[key] != local["metrics"][key]:
                raise AssertionError(f"crop/full mismatch: {label} {key}")
        viewer = None
        if not args.skip_replays:
            viewer = save_checked_replay(
                full, scenarios[0], plan, output / "full" / plan.plan_id, record
            )
        report["full_domain_comparison"][label] = {
            "evaluation": record.to_mapping(),
            "viewer": viewer,
        }
        dump(output / "report.json", report)
        print(json.dumps({"full": label, "metrics": record.metrics}), flush=True)
    if report["selection_sha256"] != digest(report["chosen_plans"]):
        raise AssertionError("validation changed selected plans")
    report.update(
        status="completed" if not args.skip_replays else "completed_no_replays",
        parent_unchanged=request_fingerprint(parent.request) == before,
        cache_hits=oracle.cache_hits,
        search_solver_calls=oracle.solver_calls,
        validation_solver_calls=validation_oracle.solver_calls,
        full_solver_calls=full_oracle.solver_calls,
    )
    if not report["parent_unchanged"]:
        raise AssertionError("base case changed")
    dump(output / "report.json", report)
    print("Completed " + str(output), flush=True)


if __name__ == "__main__":
    main()
