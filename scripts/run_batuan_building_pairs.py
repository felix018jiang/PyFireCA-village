"""Freeze and run observed-outline, synthetic-material Batuan pair experiments."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from concurrent.futures import ProcessPoolExecutor
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.planning.building_pairs import isolate_pair_request, select_building_pairs
from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.interventions.clearing import ClearingPath, ClearingPlan, prepare_clearing
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import crop_v3_case_bundle, load_v3_case_bundle

ROOT = Path(__file__).resolve().parents[1]
BASE = ROOT / "runs/batuan-slope-contact-20261016/case"


def dump(path, value):
    path = Path(path)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    temp.replace(path)


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def prepare(output, source_profile="single_cell"):
    output.mkdir(parents=True, exist_ok=False)
    bundle = load_v3_case_bundle(BASE)
    annotation = Path(bundle.metadata["spatial_assets_dir"]) / "buildings.json"
    selected, inventory = select_building_pairs(
        bundle, json.loads(annotation.read_text())["buildings"]
    )
    cases = {}
    for pair in selected:
        cells = np.argwhere(np.isin(bundle.request.building_index, pair["owner_ids"]))
        low = np.maximum(cells.min(0) - 8, 0)
        high = np.minimum(cells.max(0) + 9, bundle.request.grid.shape)
        r, c = cells[0] - low
        directory = output / "cases" / pair["pair_id"]
        crop_v3_case_bundle(
            BASE,
            directory,
            row_start=int(low[0]),
            row_stop=int(high[0]),
            col_start=int(low[1]),
            col_stop=int(high[1]),
            ignition=VillageIgnitionEvent(0, int(r), int(c)),
            end_time_s=5400,
        )
        cases[pair["pair_id"]] = str(directory)
    protocol = {
        "schema": "batuan-building-pairs-1",
        "selected": selected,
        "selection_rule": "gap bins x mapped ground fuel, closest to bin midpoint; no outcomes",
        "candidate_pairs": len(inventory),
        "base_sha256": request_fingerprint(bundle.request),
        "source_hashes": {
            str(p.relative_to(ROOT)): sha(p)
            for p in sorted(
                [
                    *(ROOT / "src/pyfireca/village").glob("*.py"),
                    *(ROOT / "src/pyfireca/planning").rglob("*.py"),
                    Path(__file__),
                ]
            )
        },
        "input_hashes": {
            str(p): sha(p)
            for d in [BASE, *map(Path, cases.values())]
            for p in d.glob("*")
            if p.is_file()
        },
        "annotation_sha256": sha(annotation),
        "cases": cases,
        "horizon_s": 5400,
        "source_profile": source_profile,
        "time_steps_s": [1.0, 0.5],
        "methods": ["pair_only", "mapped_ground", "clear_5m", "clear_10m"],
        "evidence": "observed_outlines_synthetic_equivalent_wood_no_field_validation",
        "controls": ("third structures inactive; ground restricted to pair box +20m; +20m guard"),
        "scope": "mechanism isolation; not full-village prediction or a safety-width standard",
        "firebrands": False,
    }
    for name in protocol["source_hashes"]:
        target = output / "source_snapshot" / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, target)
    shutil.copy2(annotation, output / "buildings.source.json")
    dump(output / "pair-inventory.json", inventory)
    dump(output / "protocol.json", protocol)
    print(
        json.dumps({"prepared": str(output), "selected": selected}, ensure_ascii=False), flush=True
    )


def run_one(task):
    output, pair, source, method, dt = task
    key = f"{pair['pair_id']}-from{source}-{method}-dt{dt:g}"
    destination = Path(output) / "records" / f"{key}.json"
    protocol = json.loads((Path(output) / "protocol.json").read_text())
    protocol_hash = sha(Path(output) / "protocol.json")
    if destination.exists():
        cached = json.loads(destination.read_text())
        if cached.get("protocol_sha256") != protocol_hash:
            raise ValueError("pair cache protocol mismatch")
        return cached
    b = load_v3_case_bundle(protocol["cases"][pair["pair_id"]])
    transport = V3RadiativeTransportParameters(**b.metadata["transport_parameters"])
    req = isolate_pair_request(
        b,
        pair,
        source,
        transport,
        ground_enabled=method != "pair_only",
        dt_s=dt,
        source_profile=protocol["source_profile"],
    )
    before = request_fingerprint(req)
    plan = ClearingPlan("none")
    if method.startswith("clear_"):
        width = float(method.split("_")[1].removesuffix("m"))
        plan = replace(
            plan,
            plan_id=method,
            paths=(ClearingPath(tuple(map(tuple, pair["band_points_m"])), width),),
        )
    req, _fraction, treatment = prepare_clearing(req, plan, transport)
    result = run_v3_simulation(req, retain_snapshots=False)
    check = check_final(req, result)
    target = next(i for i in pair["owner_ids"] if i != source)
    values = result.final_state.ignition_time_s[req.building_index == target]
    values = values[np.isfinite(values)]
    guard = np.isfinite(result.final_state.ignition_time_s)
    record = {
        "protocol_sha256": protocol_hash,
        "key": key,
        "pair_id": pair["pair_id"],
        "source_owner": source,
        "receiver_owner": target,
        "method": method,
        "dt_s": dt,
        "first_receiver_ignition_s": float(values.min()) if len(values) else None,
        "ignited_receiver_cells": len(values),
        "cleared_area_m2": treatment["cleared_area_m2"],
        "actual_horizon_s": result.final_state.time_s,
        "source_profile": protocol["source_profile"],
        "receiver_incident_radiation_J": float(
            result.final_state.incident_radiation_J[req.building_index == target].sum()
        ),
        "receiver_absorbed_radiation_J": float(
            result.final_state.absorbed_radiation_J[req.building_index == target].sum()
        ),
        "guard_ignited": bool(guard[[0, -1], :].any() or guard[:, [0, -1]].any()),
        "isolated_request_sha256": before,
        "treatment": treatment,
        "validation": check,
    }
    destination.parent.mkdir(exist_ok=True)
    dump(destination, record)
    print(
        json.dumps(
            {k: record[k] for k in ("key", "first_receiver_ignition_s", "cleared_area_m2")},
            ensure_ascii=False,
        ),
        flush=True,
    )
    return record


def run(output, workers):
    protocol = json.loads((output / "protocol.json").read_text())
    for name, h in protocol["source_hashes"].items():
        if sha(ROOT / name) != h:
            raise ValueError(f"frozen source changed: {name}")
    for name, h in protocol["input_hashes"].items():
        if sha(name) != h:
            raise ValueError(f"frozen input changed: {name}")
    tasks = [
        (str(output), p, s, m, dt)
        for p in protocol["selected"]
        for s in p["owner_ids"]
        for m in protocol["methods"]
        for dt in protocol["time_steps_s"]
    ]
    with ProcessPoolExecutor(workers) as pool:
        records = list(pool.map(run_one, tasks))
    for name, h in protocol["source_hashes"].items():
        if sha(ROOT / name) != h:
            raise ValueError(f"source changed during pair study: {name}")
    comparisons = []
    for p in protocol["selected"]:
        for source in p["owner_ids"]:
            subset = [
                r for r in records if r["pair_id"] == p["pair_id"] and r["source_owner"] == source
            ]
            stability = []
            for method in protocol["methods"]:
                a, b = sorted(
                    [r for r in subset if r["method"] == method], key=lambda r: -r["dt_s"]
                )
                x, y = a["first_receiver_ignition_s"], b["first_receiver_ignition_s"]
                stability.append(
                    {
                        "method": method,
                        "same_receiver_ignition": (x is None) == (y is None),
                        "first_time_difference_s": abs(x - y)
                        if x is not None and y is not None
                        else None,
                    }
                )
            comparisons.append(
                {"pair_id": p["pair_id"], "source_owner": source, "time_step_comparison": stability}
            )
    report = {
        "protocol_sha256": sha(output / "protocol.json"),
        "records": records,
        "comparisons": comparisons,
        "all_full_horizon": all(r["actual_horizon_s"] == 5400 for r in records),
        "any_guard_ignition": any(r["guard_ignited"] for r in records),
        "evidence": protocol["evidence"],
        "scope": protocol["scope"],
    }
    dump(output / "report.json", report)
    print(
        json.dumps(
            {
                "completed": len(records),
                "output": str(output),
                "guard": report["any_guard_ignition"],
            }
        ),
        flush=True,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["prepare", "run"], required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument(
        "--source-profile", choices=["single_cell", "source_roof_cells"], default="single_cell"
    )
    args = parser.parse_args()
    prepare(args.output.resolve(), args.source_profile) if args.phase == "prepare" else run(
        args.output.resolve(), args.workers
    )
