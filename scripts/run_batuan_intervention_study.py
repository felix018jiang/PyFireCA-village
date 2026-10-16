"""R1/R2 Batuan comparison and bounded effectiveness-only candidate search.

All lines and construction zones are proposed experiments, not surveyed
firebreaks or village boundaries. No monetary or cleared-area objective is used.
"""
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
from dataclasses import asdict
from math import cos, radians, sin
from pathlib import Path

import numpy as np
from render_batuan_intervention_report import geographic_feature
from shapely import contains_xy
from shapely.geometry import Polygon, mapping
from shapely.ops import unary_union

from pyfireca.planning.contracts import Ignition
from pyfireca.planning.effectiveness import effectiveness_score
from pyfireca.planning.engines.village import VillageEvaluationEngine, request_fingerprint
from pyfireca.planning.evaluation import EvaluationScenario
from pyfireca.planning.interventions.clearing import (
    ClearingPlan,
    ClearingStrip,
    InterventionConflict,
)
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import (
    crop_v3_case_bundle,
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ((160, 151), (162, 151), (161, 150))


def dump(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def candidate(grid, zone, row, width, angle, length=150.0):
    x, y = grid.center(row, 151)
    dx, dy = cos(radians(angle)) * length / 2, sin(radians(angle)) * length / 2
    return ClearingPlan(
        f"{zone}-r{row}-w{width:g}-a{angle}",
        (ClearingStrip((x - dx, y - dy), (x + dx, y + dy), float(width)),),
        zone,
    )


def zones_for(grid, near_buildings):
    rr, cc = np.indices(grid.shape)
    x = grid.x_min_m + (cc + 0.5) * grid.cell_size_m
    y = grid.y_max_m - (rr + 0.5) * grid.cell_size_m
    inner = contains_xy(near_buildings, x, y)
    return {"inner_proxy": inner, "outer_proxy": ~inner}


def engine_for(bundle, near_buildings, types, code):
    return VillageEvaluationEngine(
        bundle,
        V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"]),
        zones=zones_for(bundle.request.grid, near_buildings),
        structure_types=types,
        code_version=code,
    )


def save_replay(engine, scenario, plan, output):
    request, fraction, summary = engine.prepare(scenario, plan)
    meta = dict(engine.bundle.metadata)
    meta.update(
        scenario_id=f"batuan-intervention-{plan.plan_id}",
        ignition_source={
            "row": request.scenario.ignitions[0].row,
            "col": request.scenario.ignitions[0].col,
        },
        proposed_intervention=summary,
        input_summary=meta["input_summary"] + "本次为假设清理带试验；未调查或建成，保留全部建筑。",
        research_objective="effectiveness_only_no_cost_or_area_penalty",
    )
    write_v3_case_bundle(
        request,
        output / "case",
        material_labels=engine.bundle.material_labels,
        landcover_labels=engine.bundle.landcover_labels,
        metadata=meta,
    )
    np.save(output / "cleared_fraction.npy", fraction)
    dump(
        output / "intervention.geojson",
        geographic_feature(
            summary["effective_footprint"],
            {k: v for k, v in summary.items() if k not in {"effective_footprint", "strips"}},
            request.grid.crs,
        ),
    )
    _, _run = run_v3_case_bundle(output / "case", output / "run", chunk_frames=4)
    return str(output / "run/viewer")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--prepared", type=Path, default=ROOT / "runs/batuan-slope-contact-20261016"
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--horizon-s", type=float, default=3600)
    parser.add_argument("--skip-full-replays", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    parent = load_v3_case_bundle(args.prepared / "case")
    before = request_fingerprint(parent.request)
    head = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ROOT, check=True, capture_output=True, text=True
    ).stdout.strip()
    code_paths = [
        ROOT / "src/pyfireca/planning" / p
        for p in [
            "evaluation.py",
            "effectiveness.py",
            "engines/village.py",
            "interventions/clearing.py",
        ]
    ]
    code_paths.extend(
        [
            ROOT / "scripts/render_batuan_intervention_report.py",
            ROOT / "scripts/check_batuan_intervention_horizon.py",
        ]
    )
    code_hashes = {
        str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [Path(__file__), *code_paths]
    }
    for source_path in [Path(__file__), *code_paths]:
        snapshot = output / "source_snapshot" / source_path.relative_to(ROOT)
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_path, snapshot)
    code = head + ":" + hashlib.sha256(json.dumps(code_hashes, sort_keys=True).encode()).hexdigest()
    spatial = Path(parent.metadata["spatial_assets_dir"])
    structures = json.loads((spatial / "buildings.json").read_text())["buildings"]
    labels = {item["building_id"]: item["properties"]["source_label"] for item in structures}
    types = {int(k): labels[v] for k, v in parent.metadata["building_labels"].items()}
    polygons = [Polygon(p["exterior"], p["holes"]) for item in structures for p in item["polygons"]]
    # Only a proposed search-domain proxy, never a claim of a surveyed village boundary.
    near = unary_union(polygons).buffer(20)
    scenarios = [
        EvaluationScenario(
            f"forest-{r}-{c}", (Ignition(parent.request.grid.center(r, c)),), args.horizon_s
        )
        for r, c in SOURCES
    ]
    crop_v3_case_bundle(
        args.prepared / "case",
        output / "search-case",
        row_start=120,
        row_stop=201,
        col_start=111,
        col_stop=192,
        end_time_s=args.horizon_s,
        sample_interval_s=parent.request.sample_interval_s,
    )
    bundle = load_v3_case_bundle(output / "search-case")
    engine = engine_for(bundle, near, types, code)
    empty = ClearingPlan("no-clearing")
    single = candidate(parent.request.grid, "outer_proxy", 157, 10, 0)
    plans = [
        candidate(parent.request.grid, zone, row, width, angle)
        for zone, rows in [("outer_proxy", (156, 158)), ("inner_proxy", (150, 153))]
        for row in rows
        for width in (5, 10)
        for angle in (-30, 0, 30)
    ]
    all_plans = [empty, single, *plans]
    protocol = {
        "schema": "batuan-intervention-study-1",
        "source": str(args.prepared.resolve()),
        "code": code,
        "source_hashes": code_hashes,
        "case_sha256": before,
        "scope": "local_calm_forest_ignitions_south_of_annotated_structures",
        "objective": "effectiveness_only_no_cost_or_area_penalty",
        "ranking": [
            "worst_ignited_houses",
            "mean_ignited_houses",
            "mean_ignited_structure_area_m2",
            "mean_ignited_bridges",
            "negative_mean_house_ignition_free_time_s",
        ],
        "training_scenarios": [asdict(s) for s in scenarios[:2]],
        "held_out_scenario": asdict(scenarios[2]),
        "plans": [p.to_mapping() for p in all_plans],
        "zone_assumption": (
            "20m buffer of annotated structures at cell centres; not surveyed village boundary"
        ),
        "search_space": (
            "one 150m strip, listed positions and angles, widths 5 or 10m, no area budget"
        ),
        "hypothetical_clearance": True,
        "wind": "parent calm scenario; no dynamic wind or spotting",
        "radiation_radius_m": parent.metadata["transport_parameters"]["exposure_radius_m"],
        "results_interpretation": (
            "best within finite candidates and local scenarios, not a field-optimal firebreak"
        ),
    }
    dump(output / "protocol.json", protocol)
    dump(
        output / "search-zones.geojson",
        geographic_feature(mapping(near), {"status": "proxy"}, parent.request.grid.crs),
    )
    evaluations, rejected, ranked = {}, [], []
    for plan in all_plans:
        try:
            # Reject ignition conflicts across training AND held-out scenarios before search.
            for scenario in scenarios:
                engine.prepare(scenario, plan)
            records = [engine.evaluate(s, plan) for s in scenarios[:2]]
            score = effectiveness_score(records)
        except InterventionConflict as exc:
            rejected.append({"plan_id": plan.plan_id, "reason": str(exc)})
            continue
        evaluations[plan.plan_id] = records
        ranked.append(
            {
                "plan_id": plan.plan_id,
                "zone": plan.zone_id,
                "score": score,
                "cleared_area_m2": records[0].intervention["cleared_area_m2"],
            }
        )
        dump(output / (plan.plan_id + ".json"), [item.to_mapping() for item in records])
        print(
            json.dumps(
                {
                    "plan": plan.plan_id,
                    "score": score,
                    "area_m2_record_only": records[0].intervention["cleared_area_m2"],
                }
            ),
            flush=True,
        )
    selected = {}
    for zone in ("outer_proxy", "inner_proxy"):
        feasible = [
            r for r in ranked if r["zone"] == zone and r["plan_id"] in {p.plan_id for p in plans}
        ]
        if not feasible:
            raise ValueError(f"no feasible candidates in {zone}")
        best = min(tuple(r["score"]) for r in feasible)
        ties = sorted(r["plan_id"] for r in feasible if tuple(r["score"]) == best)
        selected[zone] = {"score": best, "tied_plan_ids": ties, "representative": ties[0]}
    chosen = [
        empty,
        single,
        *[next(p for p in plans if p.plan_id == v["representative"]) for v in selected.values()],
    ]
    held = {p.plan_id: engine.evaluate(scenarios[2], p).to_mapping() for p in chosen}
    if any(item["metrics"]["burning_reached_domain_edge"] for item in held.values()):
        raise AssertionError("held-out fire reached crop boundary")
    report = {
        "status": "full_validation_pending",
        "protocol": protocol,
        "ranked": ranked,
        "rejected": rejected,
        "selected": selected,
        "held_out": held,
        "parent_unchanged": request_fingerprint(parent.request) == before,
        "baseline_training": [r.to_mapping() for r in evaluations[empty.plan_id]],
        "single_strip_training": [r.to_mapping() for r in evaluations[single.plan_id]],
    }
    dump(output / "report.json", report)
    if not args.skip_full_replays:
        full = engine_for(parent, near, types, code)
        report["full_domain_comparison"] = {}
        for plan in chosen:
            record = full.evaluate(scenarios[0], plan)
            local = evaluations[plan.plan_id][0]
            for key in ("ignited_house_count", "ignited_bridge_count", "ignited_cells"):
                if record.metrics[key] != local.metrics[key]:
                    raise AssertionError(f"crop/full mismatch: {plan.plan_id} {key}")
            viewer = save_replay(full, scenarios[0], plan, output / "full" / plan.plan_id)
            report["full_domain_comparison"][plan.plan_id] = {
                "evaluation": record.to_mapping(),
                "viewer": viewer,
            }
            dump(output / "report.json", report)
            print(json.dumps({"full": plan.plan_id, "metrics": record.metrics}), flush=True)
    report["status"] = "completed" if not args.skip_full_replays else "local_only"
    dump(output / "report.json", report)
    print("Completed " + str(output), flush=True)


if __name__ == "__main__":
    main()
