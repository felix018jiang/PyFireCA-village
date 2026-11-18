"""Frozen, resumable bridge/house mesh controls with explicit trigger-cell evidence.

This separate experiment never changes or resumes the ongoing joint optimization.
Whole owned roofs ignite at t=0: an isolated high-exposure mechanism control,
not a replay of the external forest-fire scenarios or a field safety standard.
"""

# ruff: noqa: E501, RUF001

from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import math
import os
import shutil
import tarfile
from dataclasses import asdict, replace
from pathlib import Path
from types import SimpleNamespace

import numpy as np
from run_batuan_building_pairs import BASE, ROOT, dump, sha
from run_batuan_joint_search import digest
from run_batuan_local_mesh import clear_local_ground, outline, rebuild

from pyfireca.planning.engines.village import request_fingerprint
from pyfireca.planning.observations import build_object_observations
from pyfireca.village.core_validation_v3 import check_final
from pyfireca.village.domain import ExposureEdges
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.transport_v3 import V3RadiativeTransportParameters
from pyfireca.village.workflow_v3 import load_v3_case_bundle, write_v3_case_bundle

PARENT = ROOT / "runs/research/batuan-observed-joint-20261017-r2"
PAIR_IDS = ("B0061", "B0120")
OWNERS = (60, 119)


def verify_frozen(output):
    protocol = json.loads((output / "protocol.json").read_text())
    for name, expected in {**protocol["source_hashes"], **protocol["input_hashes"]}.items():
        if sha(ROOT / name) != expected:
            raise ValueError(f"frozen source/input changed: {name}")
    for name, expected in protocol["source_hashes"].items():
        if sha(output / "source_snapshot" / name) != expected:
            raise ValueError(f"frozen source snapshot changed: {name}")
    if sha(output / "buildings.source.json") != protocol["annotation_sha256"]:
        raise ValueError("annotation snapshot changed")
    for name, expected in json.loads((output / "mesh-inputs.json").read_text()).items():
        if sha(output / name) != expected:
            raise ValueError(f"frozen mesh changed: {name}")
    return protocol


def controls():
    tasks = [
        {
            "spacing_m": spacing,
            "ground_mode": mode,
            "source_owner": source,
            "cleared": cleared,
            "dt_s": 1.0,
            "channel": "both",
        }
        for spacing in (5, 2.5, 1)
        for source in OWNERS
        for mode, cleared in (("none", False), ("mapped", False), ("mapped", True))
    ]
    tasks.extend(
        {
            "spacing_m": 1,
            "ground_mode": "none",
            "source_owner": source,
            "cleared": False,
            "dt_s": dt,
            "channel": channel,
        }
        for source in OWNERS
        for dt, channel in ((0.5, "both"), (1.0, "radiation_only"), (1.0, "contact_only"))
    )
    for task in tasks:
        task["key"] = (
            f"{task['spacing_m']:g}m-{task['ground_mode']}-from{task['source_owner']}"
            f"-clear{int(task['cleared'])}-dt{task['dt_s']:g}-{task['channel']}"
        )
    return tasks


def prepare(output):
    parent = json.loads((PARENT / "protocol.json").read_text())
    for name, expected in {**parent["source_hashes"], **parent["input_hashes"]}.items():
        if sha(ROOT / name) != expected:
            raise ValueError(f"parent source/input changed: {name}")
    bundle = load_v3_case_bundle(BASE)
    if request_fingerprint(bundle.request) != parent["base_fingerprint"]:
        raise ValueError("base case differs from parent")
    if sha(PARENT / "buildings.source.json") != parent["annotation_sha256"]:
        raise ValueError("parent annotation snapshot changed")
    structures = json.loads((PARENT / "buildings.source.json").read_text())["buildings"]
    labels = bundle.metadata["building_labels"]
    if tuple(labels[str(i)] for i in OWNERS) != PAIR_IDS:
        raise ValueError("explicit pair identities do not match input labels")
    shapes = {s["building_id"]: outline(s) for s in structures if s["building_id"] in PAIR_IDS}
    pair = {
        "pair_id": "B0061-B0120",
        "building_ids": list(PAIR_IDS),
        "owner_ids": list(OWNERS),
        "polygon_gap_m": shapes[PAIR_IDS[0]].distance(shapes[PAIR_IDS[1]]),
    }
    if shapes[PAIR_IDS[0]].intersection(shapes[PAIR_IDS[1]]).area > 1e-8:
        raise ValueError("pair outlines overlap; independent gap control not justified")
    transport = replace(
        V3RadiativeTransportParameters(**bundle.metadata["transport_parameters"]),
        wind_speed_m_s=4,
        wind_from_deg=225,
        flame_tilt_coefficient_s_m=0.2,
    )
    bundle = replace(
        bundle, metadata={**bundle.metadata, "transport_parameters": asdict(transport)}
    )
    output.mkdir(parents=True, exist_ok=False)
    (output / "records").mkdir()
    shutil.copy2(PARENT / "buildings.source.json", output / "buildings.source.json")
    source_names = set(parent["source_hashes"]) | {
        "scripts/run_batuan_bridge_mesh.py",
        "scripts/run_batuan_local_mesh.py",
    }
    protocol = {
        "schema": "batuan-bridge-mesh-1",
        "pair": pair,
        "tasks": controls(),
        "horizon_s": 5400,
        "parent_protocol_sha256": sha(PARENT / "protocol.json"),
        "annotation_sha256": parent["annotation_sha256"],
        "source_profile": "all owned source-roof cells at t=0; isolated high-exposure control",
        "wind": {
            "speed_m_s": 4,
            "from_deg": 225,
            "tilt_coefficient_s_m": 0.2,
            "scope": "static radiation tilt only; matches sw4 stress setting, not external-fire replay",
        },
        "policy": "fixed moisture and synthetic equivalent timber; no firebrands/demolition/money",
        "ground": "inherited 5m inferred map; newly exposed old roof gaps inert and unobserved",
        "terrain": "inherited approximately 30m FABDEM; bilinear interpolation adds no measurements",
        "scope": "two active objects with local ground; other buildings inert mechanism controls",
        "clearing": "all active local ground removal, no opaque wall; not an optimized construction strip",
        "radiation_regularization": "fixed parent 5m metric scale; unchanged at fine mesh",
        "primary_receiver_metric": "owned receiver cells; annotated shared-cell triggers reported separately",
        "trigger_diagnostics": "first ignition cells; radiation/contact arrays are end-of-run cumulative values",
        "source_hashes": {name: sha(ROOT / name) for name in sorted(source_names)},
        "input_hashes": {
            str(p): sha(p)
            for p in [*BASE.glob("*"), PARENT / "protocol.json", PARENT / "buildings.source.json"]
            if p.is_file()
        },
    }
    dump(output / "protocol.json", protocol)
    for name in source_names:
        dest = output / "source_snapshot" / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, dest)
    for spacing in (5, 2.5, 1):
        for mode in ("none", "mapped"):
            q, audit, transport = rebuild(bundle, pair, structures, spacing, mode)
            if spacing == 5:
                for delta in (
                    q.grid.x_min_m - bundle.request.grid.x_min_m,
                    q.grid.y_max_m - bundle.request.grid.y_max_m,
                ):
                    if abs(delta / 5 - round(delta / 5)) > 1e-8:
                        raise AssertionError("coarse grid phase differs from full domain")
            selected = [s for s in structures if s["building_id"] in PAIR_IDS]
            observer = build_object_observations(
                q, selected, {str(i): labels[str(i)] for i in OWNERS}
            )
            audit["annotation_observation"] = observer.summary()
            write_v3_case_bundle(
                q,
                output / "cases" / f"{spacing:g}m-{mode}",
                metadata={"mesh_audit": audit, "transport_parameters": asdict(transport)},
            )
            print(json.dumps({"prepared": f"{spacing:g}m-{mode}", "mesh": audit}), flush=True)
    dump(
        output / "mesh-inputs.json",
        {
            str(p.relative_to(output)): sha(p)
            for p in sorted((output / "cases").rglob("*"))
            if p.is_file()
        },
    )


def first_trigger_cells(q, state, cell_ids, areas):
    times = state.ignition_time_s.ravel()[cell_ids]
    finite = np.isfinite(times)
    if not finite.any():
        return {"time_s": None, "cells": []}
    first = float(times[finite].min())
    selected = np.flatnonzero(finite & (np.abs(times - first) < 1e-8))
    rows = []
    for index in selected:
        cid = int(cell_ids[index])
        r, c = divmod(cid, q.grid.width)
        rows.append(
            {
                "row": r,
                "column": c,
                "point_m": list(q.grid.center(r, c)),
                "fuel_owner": int(q.building_index[r, c]),
                "receiver_intersection_m2": float(areas[index]),
                "end_cumulative_absorbed_radiation_J": float(state.absorbed_radiation_J[r, c]),
                "end_cumulative_received_contact_J": float(state.received_convection_J[r, c]),
            }
        )
    return {"time_s": first, "cells": rows}


def task_request(output, task):
    bundle = load_v3_case_bundle(output / "cases" / f"{task['spacing_m']:g}m-{task['ground_mode']}")
    q = clear_local_ground(bundle.request) if task["cleared"] else bundle.request
    source = task["source_owner"]
    events = tuple(
        VillageIgnitionEvent(0, int(r), int(c)) for r, c in np.argwhere(q.building_index == source)
    )
    if not events:
        raise ValueError("source has no owned combustible roof cells")
    q = replace(q, scenario=replace(q.scenario, max_dt_s=task["dt_s"], ignitions=events))
    if task["channel"] == "radiation_only":
        q = replace(q, flame_contact=None)
    elif task["channel"] == "contact_only":
        q = replace(
            q,
            exposure_edges=ExposureEdges(
                np.array([], dtype=np.int64),
                np.array([], dtype=np.int64),
                np.array([], dtype=float),
                q.exposure_edges.cell_count,
            ),
        )
    return bundle, q


def run_one(output, protocol, task):
    dest = output / "records" / f"{task['key']}.json"
    if dest.exists():
        entry = json.loads(dest.read_text())
        if entry["record_sha256"] != digest(entry["record"]) or entry["record"][
            "protocol_sha256"
        ] != sha(output / "protocol.json"):
            raise ValueError("record cache checksum/protocol mismatch")
        return entry["record"]
    bundle, q = task_request(output, task)
    pair = protocol["pair"]
    receiver = next(i for i in pair["owner_ids"] if i != task["source_owner"])
    structures = [
        s
        for s in json.loads((output / "buildings.source.json").read_text())["buildings"]
        if s["building_id"] in PAIR_IDS
    ]
    observer = build_object_observations(
        q, structures, dict(zip(map(str, OWNERS), PAIR_IDS, strict=True))
    )
    result = run_v3_simulation(q, retain_snapshots=False)
    state = result.final_state
    validation = check_final(q, result)
    errors, tolerance = validation["energy_closure_errors_J"], validation["energy_tolerance_J"]
    if not math.isfinite(tolerance) or any(
        not math.isfinite(v) or abs(v) > tolerance for v in errors.values()
    ):
        raise AssertionError("absolute energy closure failed")
    owned_ids = np.flatnonzero(q.building_index.ravel() == receiver)
    obj = next(o for o in observer.objects if o.object_id == receiver)
    owned = first_trigger_cells(
        q, state, owned_ids, q.parameters.covered_area_m2.ravel()[owned_ids]
    )
    observed = first_trigger_cells(q, state, obj.cell_ids, obj.areas_m2)
    guard = np.isfinite(state.ignition_time_s)
    if (
        not math.isfinite(state.time_s)
        or abs(state.time_s - 5400) > 1e-8
        or guard[[0, -1], :].any()
        or guard[:, [0, -1]].any()
    ):
        raise AssertionError("incomplete or boundary-affected run")
    thermal = output / "thermal-state" / f"{task['key']}.npz"
    thermal.parent.mkdir(exist_ok=True)
    with thermal.with_suffix(".tmp").open("wb") as stream:
        np.savez_compressed(
            stream,
            ignition_time_s=state.ignition_time_s,
            absorbed_radiation_J=state.absorbed_radiation_J,
            received_convection_J=state.received_convection_J,
        )
    thermal.with_suffix(".tmp").replace(thermal)
    record = {
        **task,
        "receiver_owner": receiver,
        "protocol_sha256": sha(output / "protocol.json"),
        "request_sha256": request_fingerprint(q),
        "actual_horizon_s": state.time_s,
        "owned_receiver_trigger": owned,
        "annotated_receiver_trigger": observed,
        "annotation_trigger_on_source_at_t0": observed["time_s"] == 0
        and any(c["fuel_owner"] == task["source_owner"] for c in observed["cells"]),
        "receiver_owned_ignited_area_m2": float(
            q.parameters.covered_area_m2.ravel()[owned_ids][
                np.isfinite(state.ignition_time_s.ravel()[owned_ids])
            ].sum()
        ),
        "removed_ground_area_m2": bundle.metadata["mesh_audit"]["active_ground_area_m2"]
        if task["cleared"]
        else 0,
        "mesh_audit": bundle.metadata["mesh_audit"],
        "guard_ignited": False,
        "thermal_state_file": str(thermal.relative_to(output)),
        "thermal_state_sha256": sha(thermal),
        "validation": validation,
    }
    dump(dest, {"record": record, "record_sha256": digest(record)})
    print(
        json.dumps(
            {
                "completed": task["key"],
                "owned_s": owned["time_s"],
                "annotated_s": observed["time_s"],
            }
        ),
        flush=True,
    )
    return record


def finish(output, protocol, records):
    verify_frozen(output)
    if {r["key"] for r in records} != {t["key"] for t in protocol["tasks"]} or len(records) != 24:
        raise AssertionError("incomplete task matrix")
    bykey = {t["key"]: t for t in protocol["tasks"]}
    for record in records:
        if sha(output / record["thermal_state_file"]) != record["thermal_state_sha256"]:
            raise AssertionError("thermal evidence checksum mismatch")
        task = bykey[record["key"]]
        if any(record[k] != v for k, v in task.items()):
            raise AssertionError("record differs from declared task")
        _, q = task_request(output, task)
        if request_fingerprint(q) != record["request_sha256"]:
            raise AssertionError("record request identity mismatch")
        with np.load(output / record["thermal_state_file"]) as saved:
            state = SimpleNamespace(**{k: saved[k] for k in saved.files})
        receiver = record["receiver_owner"]
        ids = np.flatnonzero(q.building_index.ravel() == receiver)
        replay = first_trigger_cells(q, state, ids, q.parameters.covered_area_m2.ravel()[ids])
        if replay != record["owned_receiver_trigger"]:
            raise AssertionError("trigger differs from saved thermal evidence")
    # Clearance must reproduce the no-ground control with exactly the same buildings and transport.
    equal = []
    for task in protocol["tasks"]:
        if task["cleared"]:
            _, cleared = task_request(output, task)
            _, none = task_request(output, {**task, "ground_mode": "none", "cleared": False})
            if request_fingerprint(cleared) != request_fingerprint(none):
                raise AssertionError("ground removal changed building-to-building physics")
            equal.append(task["key"])
    report = {
        "protocol_sha256": sha(output / "protocol.json"),
        "records": records,
        "cleared_equals_no_ground_checks": equal,
        "all_full_90min": True,
        "scope": protocol["scope"],
        "field_accuracy_claimed": False,
    }
    dump(output / "report.json", report)
    rows = [
        "# 风雨桥附近细网格：独立机制核验",
        "",
        "24项完整90分钟高暴露对照；SW4静态辐射倾斜，固定水分与等效合成木材。不是整村外来火的重放或施工安全标准。",
        "",
        "| 网格m | 源编号 | 地表 | 清理 | 通道 | dt秒 | 自有元胞首次引燃秒 | 标注观测首次引燃秒 |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in records:

        def val(v):
            return "90分钟未引燃" if v is None else f"{v:.2f}"

        rows.append(
            f"| {r['spacing_m']} | {r['source_owner']} | {r['ground_mode']} | {r['cleared']} | {r['channel']} | {r['dt_s']} | {val(r['owned_receiver_trigger']['time_s'])} | {val(r['annotated_receiver_trigger']['time_s'])} |"
        )
    rows.extend(
        [
            "",
            "编号60=B0061民居，119=B0120风雨桥。共享格导致的t=0标注触发单独保留，不解释成跨间隙物理传播。清理不生成辐射墙。首引燃格及完整引燃时间数组可追溯；热通量字段为末时累计量，不是引燃瞬间的通道分解。",
            "",
            "其他建筑是不活动机制控制；地表继承5米推测分类，DEM原生约30米，新露出屋面间隙无实测地面材质。没有因细化网格而添加实测资料。",
        ]
    )
    (output / "RESULTS.md").write_text("\n".join(rows) + "\n")
    # Independent archive; never commits or rewrites the running joint study.
    backup = ROOT.parent / "PyFireCA-backups" / output.name
    backup.mkdir(exist_ok=True)
    archive = backup / "bridge-mesh-experiment.tar.gz"
    files = [
        p
        for p in output.rglob("*")
        if p.is_file()
        and p.suffix not in {".lock", ".tmp"}
        and p.name not in {"status.json", "completion.json"}
    ]
    manifest = {str(p.relative_to(output)): sha(p) for p in files}
    with tarfile.open(archive, "w:gz") as tar:
        for p in files:
            tar.add(p, arcname=str(p.relative_to(output)), recursive=False)
    with tarfile.open(archive) as tar:
        for name, expected in manifest.items():
            if hashlib.sha256(tar.extractfile(name).read()).hexdigest() != expected:
                raise AssertionError("backup byte verification failed")
    dump(
        backup / "backup-validation.json",
        {"files": manifest, "archive_sha256": sha(archive), "verified": True},
    )
    dump(
        output / "completion.json",
        {
            "count": len(records),
            "backup": str(backup),
            "report_sha256": sha(output / "report.json"),
            "automatic_commit": False,
        },
    )


def run(output):
    with (output / "pipeline.lock").open("a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            print("Existing bridge experiment holds the lock; no duplicate started.")
            return
        if (output / "completion.json").exists():
            print("Bridge experiment already complete.")
            return
        protocol = verify_frozen(output)
        records = []
        for task in protocol["tasks"]:
            dump(
                output / "status.json",
                {
                    "pid": os.getpid(),
                    "stage": "running",
                    "completed": len(records),
                    "total": 24,
                    "current": task["key"],
                },
            )
            records.append(run_one(output, protocol, task))
        finish(output, protocol, records)
        dump(output / "status.json", {"stage": "complete", "completed": 24, "total": 24})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--phase", choices=["prepare", "run", "all"], required=True)
    args = parser.parse_args()
    output = args.output.resolve()
    try:
        if args.phase in {"prepare", "all"} and not output.exists():
            prepare(output)
        if args.phase in {"run", "all"}:
            run(output)
    except Exception as error:
        if output.exists():
            dump(output / "pipeline-error.json", {"pid": os.getpid(), "error": str(error)})
        raise
