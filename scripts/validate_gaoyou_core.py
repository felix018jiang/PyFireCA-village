"""Automatic core acceptance: controls, representative crops, full runs and replay parity."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import uuid
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.core_validation_v3 import check_final, compare_steps
from pyfireca.village.ignition import VillageIgnitionEvent
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import (
    crop_v3_case_bundle,
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)

ROOT = Path(__file__).resolve().parents[1]


def pick_points(request):
    fuel = request.burnable_mask
    material = request.material_index
    faces = np.zeros(fuel.shape, int)
    building_faces = np.zeros(fuel.shape, int)
    for dr, dc in [(1, 0), (-1, 0), (0, 1), (0, -1)]:
        shifted = np.roll(fuel, (dr, dc), (0, 1))
        buildings = np.roll(material == 5, (dr, dc), (0, 1))
        if dr == 1:
            shifted[0] = False
            buildings[0] = False
        if dr == -1:
            shifted[-1] = False
            buildings[-1] = False
        if dc == 1:
            shifted[:, 0] = False
            buildings[:, 0] = False
        if dc == -1:
            shifted[:, -1] = False
            buildings[:, -1] = False
        faces += shifted
        building_faces += buildings
    rr, cc = np.indices(fuel.shape)
    distance = (rr - fuel.shape[0] / 2) ** 2 + (cc - fuel.shape[1] / 2) ** 2
    interior = (rr >= 20) & (rr < fuel.shape[0] - 20) & (cc >= 20) & (cc < fuel.shape[1] - 20)
    categories = {
        "grass": 1,
        "shrub": 2,
        "tree": 4,
        "building": 5,
        "vegetation_building_interface": None,
    }
    points = []
    for name, code in categories.items():
        eligible = fuel & interior
        eligible &= ((material != 5) & (building_faces > 0)) if code is None else material == code
        coords = np.argwhere(eligible)
        if not coords.size:
            raise ValueError(f"no representative fuel for {name}")
        ranks = np.lexsort((coords[:, 1], coords[:, 0], distance[eligible], -faces[eligible]))
        row, col = map(int, coords[ranks[0]])
        points.append(
            {
                "name": name,
                "row": row,
                "col": col,
                "material": int(material[row, col]),
                "face_fuel_neighbors": int(faces[row, col]),
                "face_building_neighbors": int(building_faces[row, col]),
            }
        )
    return points


def check_replay(run, request, result):
    viewer = run / "viewer"
    manifest = json.loads((viewer / "manifest.json").read_text())
    cells = request.grid.height * request.grid.width
    frames = manifest["frame_count"]
    types = {"uint8": "u1", "float32-le": "<f4", "float64-le": "<f8", "int32-le": "<i4"}

    def dynamic(name):
        spec = manifest[name]
        dtype = types[spec["dtype"]]
        paths = [item["path"] for item in spec["chunks"]] if "chunks" in spec else [spec["path"]]
        for path in paths:
            values = np.fromfile(viewer / path, dtype=dtype)
            if values.size % cells:
                raise AssertionError(f"bad replay dimensions: {path}")
            yield from values.reshape(-1, cells)

    previous = None
    burned = None
    max_mass_error = 0.0
    summaries = []
    initial = result.initial_fuel_kg.ravel()
    records = zip(
        *(
            dynamic(name)
            for name in [
                "state",
                "fuel",
                "temperature",
                "temperature_valid",
                "hrr",
                "surface_water",
            ]
        ),
        strict=True,
    )
    for i, (state, fuel, temp, valid, hrr, water) in enumerate(records):
        if not np.isfinite(fuel).all() or np.any(fuel < 0) or np.any(fuel > 1 + 1e-7):
            raise AssertionError("bad replay fuel fraction")
        if previous is not None and np.any(fuel > previous + 1e-7):
            raise AssertionError("replay fuel increases")
        if np.any(fuel[state == 4] != 0):
            raise AssertionError("burned-out replay fuel")
        if burned is not None and (np.any(state[burned] != 4) or np.any(hrr[burned] != 0)):
            raise AssertionError("burned-out frame revives or releases heat")
        if np.any(~np.isfinite(temp[valid.astype(bool)])) or np.any(
            temp[valid.astype(bool)] < request.scenario.ambient_temperature_K - 1e-4
        ):
            raise AssertionError("bad valid replay temperature")
        if (
            np.any(~np.isfinite(hrr))
            or np.any(hrr < 0)
            or np.any(~np.isfinite(water))
            or np.any(water < 0)
        ):
            raise AssertionError("bad replay heat/water")
        if np.any(fuel[~request.burnable_mask.ravel()]):
            raise AssertionError("inert replay acquired fuel")
        previous = fuel.copy()
        burned = state == 4
        summaries.append(
            {
                "frame": i,
                "time_s": manifest["sample_times_s"][i],
                "heating": int((state == 2).sum()),
                "burning": int((state == 3).sum()),
                "burned_out": int((state == 4).sum()),
                "remaining_fuel_kg": float(np.sum(initial * fuel)),
            }
        )
    if len(summaries) != frames:
        raise AssertionError("replay frame count mismatch")
    final = result.final_state
    expected = {
        "state": final.state,
        "temperature": final.surface_temperature_K,
        "temperature_valid": final.temperature_valid_mask,
        "hrr": final.interval_mean_hrr_W,
        "surface_water": final.heated_water_remaining_kg,
        "fuel": np.divide(
            final.fuel_remaining_kg,
            result.initial_fuel_kg,
            out=np.zeros_like(result.initial_fuel_kg),
            where=result.initial_fuel_kg > 0,
        ),
    }
    for name, values in expected.items():
        last = None
        for field_frame in dynamic(name):
            last = field_frame
        np.testing.assert_array_equal(last, values.ravel().astype(types[manifest[name]["dtype"]]))
    max_mass_error = abs(summaries[-1]["remaining_fuel_kg"] - float(final.fuel_remaining_kg.sum()))
    if max_mass_error > max(1e-5, float(initial.sum()) * 2e-7):
        raise AssertionError("replay mass precision exceeded float32 allowance")
    return {
        "frames_checked": frames,
        "six_final_fields_match_solver_after_serialization": True,
        "last_frame_mass_rounding_error_kg": max_mass_error,
        "frame_metrics": summaries,
    }


def audit(output, source, end_time_s=1200):
    output.mkdir(parents=True, exist_ok=False)
    bundle = load_v3_case_bundle(source)
    request = bundle.request
    report = {
        "schema": "gaoyou-core-validation-1",
        "source_case": str(source.resolve()),
        "engineering_validation_only": True,
        "receiver_mode": "point",
        "end_time_s": end_time_s,
        "stages": {},
        "all_stages_completed": False,
    }
    if request.flame_contact.receiver_vertical_half_extent_m != 0:
        raise ValueError("use default point receiver baseline")

    def save():
        (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")

    save()
    spec = importlib.util.spec_from_file_location(
        "core_controls", ROOT / "examples/village/vegetation_v3/audit_core.py"
    )
    core = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(core)
    controls = []
    for name in core.CASES:
        r = core.make_core_request(name)
        if name == "heat_without_ignition":
            r = replace(r, scenario=replace(r.scenario, end_time_s=1200))
        x = run_v3_simulation(r)
        record = core.check_core_result(name, r, x)
        record.update(check_final(r, x))
        if name == "heat_without_ignition":
            burnout = x.final_state.burnout_time_s[0, 0]
            cooling = [
                s.surface_temperature_K[0, 1]
                for s in x.snapshots
                if s.time_s > burnout + r.scenario.max_dt_s
            ]
            if not cooling or np.any(np.diff(cooling) > 1e-8) or cooling[-1] >= max(cooling):
                raise AssertionError("subthreshold target failed to cool after source burnout")
            record["post_source_burnout_monotone_cooling"] = True
        controls.append(record)
    r = replace(
        core.fixture.make_request(("building", "building"), end_time_s=3600),
        flame_contact=request.flame_contact,
        sample_interval_s=10,
    )
    x = run_v3_simulation(r)
    controls.append({"name": "building_source", **check_final(r, x)})
    if not np.isfinite(x.final_state.ignition_time_s).all():
        raise AssertionError("building source control failed to ignite target")
    report["stages"]["controls"] = controls
    save()
    print("控制场景完成", flush=True)
    points = pick_points(request)
    report["points"] = points
    save()
    crops = []
    for point in points:
        name = point["name"]
        row, col = point["row"], point["col"]
        folder = output / "crops" / name
        case = crop_v3_case_bundle(
            source,
            folder / "base",
            row_start=row - 20,
            row_stop=row + 21,
            col_start=col - 20,
            col_stop=col + 21,
            end_time_s=end_time_s,
            sample_interval_s=30,
            ignition=VillageIgnitionEvent(0, 20, 20),
        )
        r = load_v3_case_bundle(case).request
        results = []
        records = []
        for dt in (1.0, 0.5, 0.25):
            q = replace(r, scenario=replace(r.scenario, max_dt_s=dt))
            x = run_v3_simulation(q, retain_snapshots=False)
            records.append({"dt_s": dt, **check_final(q, x)})
            np.save(
                folder / f"ignition-{dt}.npy", x.final_state.ignition_time_s, allow_pickle=False
            )
            results.append(x)
        comparisons = {
            "1_vs_025": compare_steps(results[0], results[2]),
            "05_vs_025": compare_steps(results[1], results[2]),
        }
        edge = np.zeros(r.grid.shape, bool)
        edge[[0, -1], :] = True
        edge[:, [0, -1]] = True
        record = {
            "point": point,
            "runs": records,
            "comparisons": comparisons,
            "ignited_crop_boundary_cells": int(
                np.isfinite(results[-1].final_state.ignition_time_s[edge]).sum()
            ),
            "crop_isolated_from_external_fuel": True,
        }
        crops.append(record)
        report["stages"]["local_time_steps"] = crops
        save()
        print(f"局部完成 {name}: {[r['ignited_cells'] for r in records]}", flush=True)
    full = []
    for point in points:
        job_id = uuid.uuid4().hex
        folder = output / "full" / job_id
        q = replace(
            request,
            scenario=replace(
                request.scenario,
                end_time_s=end_time_s,
                ignitions=(VillageIgnitionEvent(0, point["row"], point["col"]),),
            ),
            sample_interval_s=30,
        )
        metadata = {
            **bundle.metadata,
            "scenario_id": f"core-acceptance-{point['name']}",
            "validation_category": point["name"],
            "ignition_source": {"row": point["row"], "col": point["col"]},
            "engineering_validation_only": True,
        }
        case = write_v3_case_bundle(
            q,
            folder / "case",
            material_labels=bundle.material_labels,
            landcover_labels=bundle.landcover_labels,
            metadata=metadata,
        )
        last = [-1]

        def progress(t, end, last=last, name=point["name"]):
            value = int(t / end * 4)
            if value > last[0]:
                print(f"全村 {name}: {t / end:.0%}", flush=True)
                last[0] = value

        x, run = run_v3_case_bundle(
            case, folder / "run", chunk_frames=4, progress_callback=progress
        )
        record = {
            "point": point,
            "job_id": job_id,
            "case": str(case.resolve()),
            "run": str(run.resolve()),
            **check_final(q, x),
            "replay": check_replay(run, q, x),
        }
        full.append(record)
        report["stages"]["full_village"] = full
        save()
        print(f"全村完成 {point['name']}: {record['ignited_cells']}点燃", flush=True)
    report["all_stages_completed"] = True
    report["local_convergence_pass"] = all(
        c["comparisons"]["05_vs_025"]["engineering_tolerance_pass"] for c in crops
    )
    report["default_1s_tolerance_pass"] = all(
        c["comparisons"]["1_vs_025"]["engineering_tolerance_pass"] for c in crops
    )
    report["input_sha256"] = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest()
        for p in [*sorted(source.glob("*.npy")), source / "manifest.json"]
    }
    report["script_sha256"] = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    save()
    print("全部阶段完成", flush=True)
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "runs/gaoyou-core-full-20261006/case")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--end-time-s", type=float, default=1200)
    args = parser.parse_args()
    audit(args.output, args.source, args.end_time_s)
