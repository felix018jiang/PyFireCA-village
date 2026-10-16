"""Compare Batuan contact geometry and optionally prepare a full interactive case.

Reuse the original fuels, DEM and radiative edges; change only the explicit
near-field closure. Shared-face contact assumes continuous terrain and fuel at
cell faces. It is not a field-calibrated slope or wildfire spread-rate model.
"""
# ruff: noqa: RUF001

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from dataclasses import replace
from pathlib import Path

import numpy as np

from pyfireca.village.core_validation_v3 import check_final, compare_steps
from pyfireca.village.flame_contact_v3 import FlameContactOperator, load_flame_contact_parameters
from pyfireca.village.solver_v3 import run_v3_simulation
from pyfireca.village.workflow_v3 import (
    crop_v3_case_bundle,
    load_v3_case_bundle,
    run_v3_case_bundle,
    write_v3_case_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "examples/village/vegetation_v3/flame-contact-batuan-slope-5m.synthetic.yml"


def describe(
    request, result, *, row_offset=0, col_offset=0, building_labels=None, building_types=None
):
    record = check_final(request, result)
    state = result.final_state
    times = state.ignition_time_s
    ignited = np.isfinite(times)
    rows, cols = np.where(ignited)
    record["global_row_extent"] = [int(rows.min() + row_offset), int(rows.max() + row_offset)]
    record["global_col_extent"] = [int(cols.min() + col_offset), int(cols.max() + col_offset)]
    event = request.scenario.ignitions[0]
    record["spread_from_source_cells"] = {
        "north": int(event.row - rows.min()),
        "south": int(rows.max() - event.row),
        "west": int(event.col - cols.min()),
        "east": int(cols.max() - event.col),
    }
    record["reached_domain_edge"] = bool(ignited[[0, -1], :].any() or ignited[:, [0, -1]].any())
    buildings = ignited & (request.building_index >= 0)
    ids = np.unique(request.building_index[buildings])
    record["ignited_building_count"] = int(ids.size)
    record["ignited_house_count"] = sum(
        (building_types or {}).get(str(bid)) == "building" for bid in ids
    )
    record["ignited_bridge_count"] = sum(
        (building_types or {}).get(str(bid)) == "fengyuqiao" for bid in ids
    )
    record["ignited_building_cells"] = int(buildings.sum())
    record["building_first_ignitions"] = []
    for bid in ids:
        mask = buildings & (request.building_index == bid)
        cell = int(np.argmin(np.where(mask, times, np.inf)))
        rr, cc = divmod(cell, request.grid.width)
        record["building_first_ignitions"].append(
            {
                "building_index": int(bid),
                "building_label": (building_labels or {}).get(str(bid), str(bid)),
                "source_label": (building_types or {}).get(str(bid), "unspecified"),
                "time_s": float(times[rr, cc]),
                "row": rr + row_offset,
                "col": cc + col_offset,
                "received_convection_final_J": float(state.received_convection_J[rr, cc]),
            }
        )
    record["building_first_ignitions"].sort(key=lambda entry: entry["time_s"])
    record["contact_transferred_J"] = result.energy_budget.convective_transferred_J
    return record


def source_geometry(request):
    event = request.scenario.ignitions[0]
    row, col = event.row, event.col
    operator = FlameContactOperator.build(
        request.grid,
        request.burnable_mask,
        request.parameters,
        request.flame_contact,
        request.ground_elevation_m,
        request.material_index,
    )
    cell = row * request.grid.width + col
    records = []
    for direction, (dr, dc) in enumerate(((-1, 0), (1, 0), (0, -1), (0, 1))):
        rr, cc = row + dr, col + dc
        records.append(
            {
                "direction": ("north", "south", "west", "east")[direction],
                "ground_delta_m": float(
                    request.ground_elevation_m[rr, cc] - request.ground_elevation_m[row, col]
                ),
                "contact_edge": bool(operator.neighbors[cell, direction] >= 0),
                "geometry_fraction": float(operator.geometry_fraction[cell, direction]),
            }
        )
    return records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=ROOT / "runs/batuan-contact-20261016")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--prepared-output", type=Path)
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    source = args.source.resolve()
    parent = load_v3_case_bundle(source / "case")
    contact = load_flame_contact_parameters(CONFIG)
    event = parent.request.scenario.ignitions[0]
    r0, c0 = event.row - 20, event.col - 20
    crop_v3_case_bundle(
        source / "case",
        output / "crop",
        row_start=r0,
        row_stop=event.row + 21,
        col_start=c0,
        col_stop=event.col + 21,
        end_time_s=1800,
        sample_interval_s=60,
    )
    base = load_v3_case_bundle(output / "crop").request
    labels = parent.metadata.get("building_labels", {})
    annotated = json.loads((source / "buildings/buildings.json").read_text())["buildings"]
    label_by_id = {item["building_id"]: item["properties"]["source_label"] for item in annotated}
    building_types = {key: label_by_id[value] for key, value in labels.items()}
    results = {}
    requests = {
        "cell_center": base,
        "shared_face": replace(base, flame_contact=contact),
        "radiation_only": replace(base, flame_contact=None),
        "shared_face_half_dt": replace(
            base, flame_contact=contact, scenario=replace(base.scenario, max_dt_s=0.5)
        ),
    }
    report = {
        "source": str(source),
        "configuration": contact.to_mapping(),
        "unchanged": "fuels, moisture, ignition thresholds, DEM and radiative edges",
        "assumptions": (
            "continuous terrain and fuel at adjacent faces; contact has no separate slope "
            "enhancement, and cannot resolve subcell gaps or cliffs; DEM still affects radiation"
        ),
        "structure_counting": (
            "building_index includes both houses and the annotated wind-rain bridge"
        ),
        "local_cases": {},
    }
    for name, request in requests.items():
        result = run_v3_simulation(request, retain_snapshots=False)
        results[name] = result
        record = describe(
            request,
            result,
            row_offset=r0,
            col_offset=c0,
            building_labels=labels,
            building_types=building_types,
        )
        if request.flame_contact:
            record["source_contact_geometry"] = source_geometry(request)
        report["local_cases"][name] = record
        np.save(output / f"{name}-ignition-time-s.npy", result.final_state.ignition_time_s)
        print(
            json.dumps(
                {
                    "case": name,
                    **{
                        key: record[key]
                        for key in (
                            "ignited_cells",
                            "burned_out_cells",
                            "ignited_building_count",
                            "spread_from_source_cells",
                            "building_first_ignitions",
                        )
                    },
                },
            ),
            flush=True,
        )
    report["time_step_comparison"] = compare_steps(
        results["shared_face"], results["shared_face_half_dt"]
    )
    if not report["time_step_comparison"]["engineering_tolerance_pass"]:
        raise AssertionError("shared-face time-step comparison failed")
    (output / "report.json").write_text(json.dumps(report, indent=2))

    if args.prepared_output is None:
        return
    prepared = args.prepared_output.resolve()
    prepared.mkdir(parents=True, exist_ok=False)
    shutil.copytree(Path(parent.metadata["spatial_assets_dir"]), prepared / "spatial")
    if (source / "buildings").is_dir():
        shutil.copytree(source / "buildings", prepared / "buildings")
    request = replace(parent.request, flame_contact=contact)
    metadata = dict(parent.metadata)
    metadata.update(
        scenario_id="batuan-continuous-slope-contact-demo",
        spatial_assets_dir=str(prepared / "spatial"),
        parent_case=str(source / "case"),
        near_field_contact_config=contact.to_mapping(),
        contact_geometry_audit=str(output / "report.json"),
        contact_geometry_assumption=report["assumptions"],
        input_summary=(
            parent.metadata["input_summary"]
            + "相邻可燃格按连续坡面公共边计算接触，辐射保留DEM高差。"
        ),
        sources_sha256={
            **parent.metadata.get("sources_sha256", {}),
            str(CONFIG): hashlib.sha256(CONFIG.read_bytes()).hexdigest(),
        },
    )
    case = write_v3_case_bundle(
        request,
        prepared / "case",
        material_labels=parent.material_labels,
        landcover_labels=parent.landcover_labels,
        metadata=metadata,
    )
    # Persisted physics, terrain, masks and radiation must be byte-for-byte unchanged.
    for old in (source / "case").glob("*.npy"):
        if (
            hashlib.sha256(old.read_bytes()).digest()
            != hashlib.sha256((case / old.name).read_bytes()).digest()
        ):
            raise AssertionError(f"unexpected array change: {old.name}")
    result, _ = run_v3_case_bundle(
        case,
        prepared / "run",
        chunk_frames=4,
        progress_callback=lambda t, end: (
            print(f"full simulation {t:.0f}/{end:.0f}s", flush=True)
            if round(t) % 120 == 0
            else None
        ),
    )
    full = describe(request, result, building_labels=labels, building_types=building_types)
    # Crop was not reached; its complete ignited set must agree with the full solve.
    local_ignitions = results["shared_face"].final_state.ignition_time_s
    if report["local_cases"]["shared_face"]["reached_domain_edge"]:
        raise AssertionError("cannot use this crop as a full-domain comparison")
    np.testing.assert_array_equal(
        np.isfinite(result.final_state.ignition_time_s[r0 : r0 + 41, c0 : c0 + 41]),
        np.isfinite(local_ignitions),
    )
    if full["ignited_cells"] != report["local_cases"]["shared_face"]["ignited_cells"]:
        raise AssertionError("unexpected ignition outside crop")
    full["unchanged_case_arrays_verified"] = True
    (prepared / "summary.json").write_text(json.dumps(full, indent=2))
    report["full_domain"] = {"prepared": str(prepared), **full}
    (output / "report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps({"full_domain": full}), flush=True)


if __name__ == "__main__":
    main()
